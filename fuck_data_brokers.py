#!/usr/bin/env python3
"""f*ck_data_brokers

Data brokers collect your name, address and habits without asking, sell them to
strangers, and then act confused when you object. This tool makes them delete you.

One file. No dependencies. Everything stays on your computer.

    python fuck_data_brokers.py          manual mode: a menu, no AI needed
    python fuck_data_brokers.py mcp      let your AI drive it over MCP
    python fuck_data_brokers.py auto     the whole routine in one go; schedule it
    python fuck_data_brokers.py --help   every command

What it does:
  1. Gets the list of registered data brokers from official public registries.
  2. Writes each one a proper legal deletion and opt-out letter, in your name.
  3. When a broker insists on its web form, gives you the form and what to paste.
  4. Reads their replies and tells you what the excuse means and what to answer.
  5. Chases the ones that go quiet, and tells you where to complain.

What it never does: send your ID, send your street address, click their links,
get past a CAPTCHA for you, or believe a broker that says "we value your privacy".
"""
import argparse
import csv
import email
import email.policy
import getpass
import html
import imaplib
import io
import json
import os
import re
import secrets
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path

VERSION = '1.0.0'
DAY = 86400
REGISTRY_URL = 'https://cppa.ca.gov/data_broker_registry/registry.csv'

# ---------------------------------------------------------------- the law

# Where you live decides which law your letter quotes.
PLACES = {
    'california': ('California', 'ccpa'),
    'us_other': ('Another US state', 'us_other'),
    'quebec': ('Quebec', 'quebec'),
    'canada_other': ('Canada, outside Quebec', 'pipeda'),
    'eu': ('European Union', 'gdpr'),
    'uk': ('United Kingdom', 'uk_gdpr'),
    'brazil': ('Brazil', 'lgpd'),
    'other': ('Anywhere else', 'policy'),
}

# Days a broker gets before you chase it.
# For Brazil and for requests under a broker's own policy no law fixes the time; 30 days is how long this tool waits.
DEADLINE_DAYS = {'ccpa': 45, 'us_other': 45, 'pipeda': 30, 'quebec': 30, 'gdpr': 30, 'uk_gdpr': 30, 'lgpd': 30, 'policy': 30}

LAW_NAME = {
    'ccpa': 'California Consumer Privacy Act deletion and opt-out request',
    'us_other': 'State privacy law deletion and opt-out request',
    'pipeda': 'PIPEDA withdrawal of consent and deletion request',
    'quebec': 'Quebec Law 25 cessation of dissemination request',
    'gdpr': 'GDPR Article 17 erasure and Article 21 objection',
    'uk_gdpr': 'UK GDPR Article 17 erasure and Article 21 objection',
    'lgpd': 'LGPD Article 18 elimination and objection request',
    'policy': 'Deletion request under your privacy policy',
}

LAW_TEXT = {
    'ccpa': 'Under the California Consumer Privacy Act, Cal. Civ. Code section 1798.105, I request that you delete all personal information you hold about me and direct your service providers and contractors to do the same. Under section 1798.120 I also direct you not to sell or share my personal information. Please confirm within the time the law allows, and state the categories of information deleted or the specific reason any part of this request is declined.',
    'us_other': 'Under the privacy law of my state of residence and your own privacy policy, I request that you delete the personal information you hold about me and opt me out of the sale of personal information, targeted advertising and profiling. Please confirm the action taken or state the specific reason any part of this request is declined.',
    'pipeda': 'Under the Personal Information Protection and Electronic Documents Act (PIPEDA), I withdraw any consent to your collection, use and disclosure of my personal information (Schedule 1, Principle 4.3.8) and ask that it be destroyed, erased or made anonymous (Principle 4.5.3). Under Principle 4.9 I also ask you to confirm what personal information you hold about me and its source. Section 8 of PIPEDA requires a written response not later than thirty days after this request is received; a request that is not answered in that time is treated as refused.',
    'quebec': 'Under section 28.1 of Quebec\'s Act respecting the protection of personal information in the private sector, I request that you cease disseminating my personal information and de-index any hyperlink attached to my name that gives access to it. Under sections 28 and 30, I also request correction or deletion of the information and a written response within 30 days.',
    'gdpr': 'Under Article 17(1) of the General Data Protection Regulation, I request erasure of the personal data you hold about me, and under Article 21(1) and 21(2) I object to further processing, including for direct marketing. Article 12(3) requires a response without undue delay and in any event within one month; Article 17(2) requires you to inform other controllers to which you disclosed the data.',
    'uk_gdpr': 'Under Article 17(1) of the UK General Data Protection Regulation and the Data Protection Act 2018, I request erasure of the personal data you hold about me, and under Article 21 I object to further processing, including for direct marketing. A response is required within one month of receipt.',
    'lgpd': 'Under Article 18 of Brazil\'s General Data Protection Law (LGPD, Law No. 13,709/2018), I request the anonymization, blocking or elimination of my personal data that is unnecessary, excessive or processed in non-compliance with the Law (item IV), and the elimination of any personal data processed with my consent (item VI). I revoke any such consent (item IX). I also request information on the public and private entities with which you have shared my data (item VII). Under paragraph 2 of Article 18, I object to any processing carried out without my consent that does not comply with the Law. Under paragraph 4, if you cannot act on this at once, please reply stating that you are not the processing agent and naming that agent where possible, or giving the reasons of fact or law that prevent you.',
    'policy': 'Under your privacy policy and any privacy law that applies where I live, I request that you delete the personal information you hold about me, stop publishing it, and confirm the action taken or the reason it is declined.',
}

# Where to complain when a broker refuses. Read from each regulator's own pages, November 2026.
REGULATOR = {
    'ccpa': ('the California Privacy Protection Agency', 'File at https://cppa.ca.gov/webapplications/complaint (anyone may complain; you do not need to live in California). The Attorney General also takes complaints: https://oag.ca.gov/contact/consumer-complaint-against-business-or-company'),
    'pipeda': ('the Office of the Privacy Commissioner of Canada', 'File at https://www.priv.gc.ca/en/report-a-concern/file-a-formal-privacy-complaint/file-a-complaint-about-a-business/ . They expect you to have tried the company first (you have). Complain within six months of the refusal.'),
    'quebec': ('the Commission d\'acces a l\'information du Quebec', 'Fill in the form at https://www.cai.gouv.qc.ca/uploads/pdfs/CAI_FO_Plainte_Citoyen.pdf and email it to cai.communications@cai.gouv.qc.ca .'),
    'uk_gdpr': ('the Information Commissioner\'s Office', 'File at https://ico.org.uk/make-a-complaint/data-protection-complaints/ . Complain within three months of the company\'s response and attach your original request.'),
    'gdpr': ('your national data protection authority', 'GDPR Article 77 gives you the right to complain to the data protection authority of the country where you live or work. Every authority and its contact details are listed at https://www.edpb.europa.eu/about-edpb/about-edpb/members_en'),
    'lgpd': ('the Autoridade Nacional de Proteção de Dados (ANPD)', 'Read https://www.gov.br/anpd/pt-br/canais_atendimento/cidadao-titular-de-dados/denuncia-peticao-de-titular-referente-lgpd . A petition has to name you and include proof that you asked the company first; your letter is that proof. It is filed with a gov.br login. The ANPD says it usually does not reply to each person.'),
    'us_other': ('your state attorney general', 'Most US state privacy laws are enforced by the state attorney general. Search for your state attorney general\'s consumer complaint form.'),
    'policy': ('', 'No privacy law backs this request, so there is no regulator to complain to. The broker was asked nicely and said no. Classy.'),
}

NOTICE_DAYS = 7

# ---------------------------------------------------------------- storage


def home():
    path = Path(os.environ.get('FDB_HOME') or Path.home() / '.fuck_data_brokers')
    path.mkdir(parents=True, exist_ok=True)
    return path


def load():
    file = home() / 'state.json'
    if not file.exists():
        return {'profile': None, 'brokers': {}, 'requests': {}}
    return json.loads(file.read_text(encoding='utf-8'))


def save(state):
    file = home() / 'state.json'
    temp = file.with_suffix('.tmp')
    temp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, file)
    try:
        os.chmod(file, 0o600)
    except OSError:
        pass


def now():
    return int(time.time())


def day(stamp):
    return time.strftime('%Y-%m-%d', time.gmtime(stamp))


class Oops(Exception):
    """Something you can fix. Shown as a plain message."""


# ---------------------------------------------------------------- you


def set_profile(state, name, email, city, region, country, place):
    name, email = ' '.join(str(name).split()), str(email).strip()
    if len(name.split()) < 2:
        raise Oops('Use your full legal name, first and last. Brokers file you under it.')
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        raise Oops('That does not look like an email address.')
    if place not in PLACES:
        raise Oops('Where you live must be one of: ' + ', '.join(PLACES))
    state['profile'] = {'name': name, 'email': email, 'city': str(city).strip(), 'region': str(region).strip(),
                        'country': str(country).strip(), 'place': place}
    return state['profile']


DROP_TIP = ('You live in California, so use the state\'s own free service as well: DROP, at https://privacy.ca.gov/drop/ . One request there reaches every data broker '
            'registered in California, and they have to check it again at least every 45 days. It asks for your name, date of birth and ZIP code. '
            'This tool still covers brokers registered elsewhere, and it reads and answers what brokers write back.')


MORE = [(('california',), DROP_TIP),  # more than brokers: each step is free, and each line was read on the official page it links to
        (('california',), 'Turn on Global Privacy Control in your browser; Brave, DuckDuckGo and Firefox have it built in. California says covered businesses must honour it as your request not to sell or share your data. https://oag.ca.gov/privacy/ccpa/gpc'),
        (('california', 'us_other'), 'Stop sales calls: the National Do Not Call Registry is free and never expires. Register online, or by phone from the number you want listed. It does not stop scammers. https://consumer.ftc.gov/articles/national-do-not-call-registry-faqs'),
        (('california', 'us_other'), 'Freeze your credit: free at Equifax, Experian and TransUnion, each one separately. Nobody can open a new credit account in your name until you lift it. https://consumer.ftc.gov/articles/credit-freezes-and-fraud-alerts'),
        (('california', 'us_other'), 'Identity stolen? Report it to the FTC and get a free recovery plan with the letters already filled in. https://consumer.ftc.gov/articles/what-know-about-identity-theft'),
        (('quebec', 'canada_other'), 'Stop sales calls: the National Do Not Call List is free and your number stays on it permanently. Telemarketers get 31 days to comply; charities, political parties and pollsters are exempt. https://lnnte-dncl.gc.ca/en'),
        (('quebec', 'canada_other'), 'See what the credit bureaus hold on you: the report is free from both Equifax and TransUnion. https://www.canada.ca/en/financial-consumer-agency/services/credit-reports-score/order-credit-report.html'),
        (('uk',), 'Stop sales calls: register your landline or mobile with the Telephone Preference Service, free of charge. https://ico.org.uk/for-the-public/nuisance-calls/'),
        (('uk',), 'Stop addressed marketing mail: the Mailing Preference Service is free and takes up to three months to work fully. It does not cover mail to "The Occupier". https://www.mpsonline.org.uk/'),
        (('eu',), 'Any organisation, not only a broker, has one month to answer a deletion request (two more for a complex one) and cannot charge for an ordinary one. No answer? Complain to your data protection authority: https://www.edpb.europa.eu/about-edpb/about-edpb/members_en')]


def more(state):
    return [text for places, text in MORE if need_profile(state)['place'] in places]


def need_profile(state):
    if not state.get('profile'):
        raise Oops('Tell me who you are first: run "setup".')
    return state['profile']


def law_of(state):
    return PLACES[need_profile(state)['place']][1]


# ---------------------------------------------------------------- the brokers


def clean_email(value):
    found = re.search(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', str(value or ''))
    return found.group(0).lower() if found else ''


def rows_of(text):
    """Split a registry CSV into its rows and a way to find a column by its heading."""
    rows = list(csv.reader(io.StringIO(text.lstrip('\ufeff'))))
    if not rows:
        raise Oops('The registry file is empty.')
    header = [' '.join(h.lower().split()) for h in rows[0]]

    def column(test):
        for index, title in enumerate(header):
            if test(title):
                return index
        raise Oops('The registry changed its columns. Nothing was imported.')

    return rows[1:], column


def cell(row, index):
    return row[index].strip() if index < len(row) else ''


def reader(prefix, source, good, columns, rights_of):
    """One reader for every registry. Nothing is invented: a row without a proper number and a name is skipped.
    `columns` finds the number, name, email, website and rights headings, in that order."""
    def read(text):
        rows, column = rows_of(text)
        at, brokers = [column(test) for test in columns], {}
        for row in rows:
            number, name, mail, site, rights = (cell(row, index) for index in at)
            if good(number) and name.split():
                brokers[prefix + number.lower()] = {'name': ' '.join(name.split())[:200], 'email': clean_email(mail), 'website': site[:300], 'rights_url': rights_of(rights, site[:300]), 'source': source}
        return brokers
    return read


def page_or_site(rights, site):
    return rights[:300] if web_address(rights[:300]) else site


read_registry = reader('ca-', 'california registry', str.isdigit, [lambda h: h.startswith('broker id'), lambda h: h.startswith('data broker name'), lambda h: 'primary contact email' in h,
                       lambda h: h.startswith('data broker primary website') and 'exercise' not in h, lambda h: 'how consumers can exercise' in h], lambda rights, site: rights.split()[0][:300] if rights else '')
read_oregon = reader('or-', 'oregon registry', re.compile(r'[A-Za-z]+-\d+').fullmatch, ['license no.'.__eq__, 'facility name'.__eq__, 'opt out email'.__eq__, 'opt out website'.__eq__, 'opt out other methods'.__eq__], page_or_site)
read_texas = reader('tx-', 'texas registry', str.isdigit, ['registration number'.__eq__, 'full legal name'.__eq__, 'data broker email address'.__eq__, 'website'.__eq__, 'consumer rights link'.__eq__], page_or_site)


# Every place the list comes from. Each is an official public registry: a company is on this list because a
# government record says it is a data broker, never because a program guessed. Earlier sources win when the same
# company is registered in more than one place. To add a registry, add a reader above and one line here.
#
# "url" is set only where the registry's website allows programs to fetch the file. Oregon's website asks
# programs not to fetch from it, and Texas offers its list only as a download from a web page, so a person
# downloads those two from the official page and runs:  update-list --from or=file.csv --from tx=file.csv
SOURCES = [
    {'key': 'ca', 'name': 'California Privacy Protection Agency data broker registry', 'page': 'https://cppa.ca.gov/data_broker_registry/', 'url': REGISTRY_URL, 'read': read_registry, 'least': 100},
    {'key': 'or', 'name': 'Oregon Division of Financial Regulation data broker registry', 'page': 'https://dfr.oregon.gov/business/licensing/data-broker-registry', 'url': '', 'read': read_oregon, 'least': 50},
    {'key': 'tx', 'name': 'Texas Secretary of State data broker registry', 'page': 'https://www.sos.state.tx.us/statdoc/data-brokers.shtml', 'url': '', 'read': read_texas, 'least': 50},
]
REGISTRY_CREDIT = 'Broker list: ' + '; '.join(source['name'] for source in SOURCES) + '.'
BUNDLED_LIST = Path(__file__).resolve().parent / 'brokers.json'
MORE_LIST = Path(__file__).resolve().parent / 'more_brokers.json'


def name_key(name):
    """A company name boiled down, so "Acme Data, Inc." and "ACME DATA LLC" are seen as the same company."""
    text = re.sub(r'\bd/?b/?a\b.*$', '', str(name).lower())
    text = re.sub(r'[^a-z0-9 ]', ' ', text)
    text = re.sub(r'\b(?:inc|incorporated|llc|corp|corporation|co|company|ltd|limited|lp|llp|plc|gmbh|bv|the)\b', ' ', text)
    return ' '.join(text.split())


def fetch(url, known=None):
    """Download a registry politely: say who is asking, and tell the server what we already have so an
    unchanged file costs it nothing. Returns (None, known) when the file has not changed."""
    headers = {'Accept': 'text/csv,*/*', 'User-Agent': 'fuck_data_brokers/' + VERSION + ' (public registry list updater)'}
    for field, header in (('etag', 'If-None-Match'), ('last_modified', 'If-Modified-Since')):
        if known and known.get(field):
            headers[header] = known[field]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
            text = response.read(16 * 1024 * 1024).decode('utf-8', 'replace')
            return text, {'etag': response.headers.get('ETag') or '', 'last_modified': response.headers.get('Last-Modified') or ''}
    except urllib.error.HTTPError as problem:
        if problem.code == 304:
            return None, known
        raise


def read_more():
    """Brokers people reported that are on no registry. Each needs a name, a way to reach it, and evidence."""
    if not MORE_LIST.exists():
        return {}
    brokers = {}
    for entry in json.loads(MORE_LIST.read_text(encoding='utf-8')).get('brokers', []):
        name, email = ' '.join(str(entry.get('name', '')).split()), clean_email(entry.get('email'))
        page = web_address(entry.get('rights_url')) or web_address(entry.get('website'))
        if name_key(name) and (email or page) and web_address(entry.get('evidence')):
            brokers['more-' + name_key(name).replace(' ', '-')] = {'name': name[:200], 'email': email, 'website': str(entry.get('website') or '')[:300],
                                                                  'rights_url': str(entry.get('rights_url') or '')[:300], 'source': 'reported, with evidence'}
    return brokers


def gather(old=None, fetcher=None, conditional=True, files=None):
    """Build the list from every source. A source that fails, or comes back nearly empty, keeps what it had
    last time, so one broken registry never empties the list. `files` gives a downloaded file for a source
    that is refreshed by hand. Returns the list and what went wrong."""
    old, fetcher, files = old or {}, fetcher or fetch, files or {}
    old_brokers, old_sources = old.get('brokers') or {}, old.get('sources') or {}
    brokers, sources, problems = {}, {}, []
    for source in SOURCES:
        key = source['key']
        kept = {k: dict(v) for k, v in old_brokers.items() if k.startswith(key + '-')}
        known = old_sources.get(key) or {}
        seen, captured = known, known.get('captured', '')
        try:
            if key in files:
                found, captured = source['read'](Path(files[key]).read_text(encoding='utf-8-sig', errors='replace')), day(now())
            elif source['url']:
                text, seen = fetcher(source['url'], known if conditional and kept else None)
                found = kept if text is None else source['read'](text)
            else:
                found = kept  # refreshed by hand; nothing is fetched
            if found is not kept and len(found) < source['least']:
                raise Oops('it returned only ' + str(len(found)) + ' brokers')
        except Exception as problem:
            problems.append(source['name'] + ': ' + (str(problem) or type(problem).__name__)[:160])
            found, seen, captured = kept, known, known.get('captured', '')
        brokers.update(found)
        sources[key] = {'name': source['name'], 'page': source['page'], 'url': source['url'], 'brokers': len(found),
                        'etag': (seen or {}).get('etag', ''), 'last_modified': (seen or {}).get('last_modified', ''), 'captured': captured}
    brokers.update(read_more())
    # The same company registered in two places is written to once. The first source to list it keeps it.
    order, first = [source['key'] for source in SOURCES] + ['more'], {}
    for key in sorted(brokers, key=lambda k: (order.index(k.split('-')[0]) if k.split('-')[0] in order else len(order), k)):
        broker = brokers[key]
        broker.pop('same_as', None)
        same = first.setdefault(name_key(broker['name']) or key, key)
        if same != key:
            broker['same_as'] = same
    return {'brokers': brokers, 'sources': sources}, problems


def usable(brokers):
    return {key: {k: v for k, v in broker.items() if k != 'same_as'} for key, broker in brokers.items() if not broker.get('same_as')}


def write_list(file, files=None):
    """Used by the scheduled GitHub Action: rebuild brokers.json from every source and say what changed."""
    target = Path(file)
    old = json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
    new, problems = gather(old, files=files)
    if not new['brokers']:
        raise Oops('No source could be reached and there is no earlier list. Nothing was written.')
    before, after = usable(old.get('brokers') or {}), usable(new['brokers'])
    added = sorted(after[key]['name'] for key in after if key not in before)
    gone = sorted(before[key]['name'] for key in before if key not in after)
    with open(target, 'w', encoding='utf-8', newline='\n') as out:  # the same bytes on every system, so the file only changes when a registry does
        out.write(json.dumps({'credit': REGISTRY_CREDIT, **new}, indent=1, ensure_ascii=False, sort_keys=True) + '\n')
    line = 'Broker list: ' + str(len(after)) + ' brokers'
    if added or gone:
        line += ', ' + str(len(added)) + ' new, ' + str(len(gone)) + ' gone'
    return {'brokers': len(after), 'with_email': sum(1 for b in after.values() if b['email']), 'listed_twice': len(new['brokers']) - len(after),
            'new': added, 'gone': gone, 'problems': problems, 'line': line}


def import_registry(state, text=None):
    """With text: read one California registry file (used by the tests). Without: fetch every source."""
    if text is None:
        return load_brokers(state)
    found = read_registry(text)
    if len(found) < 20:
        raise Oops('The registry returned almost nothing, so nothing was imported.')
    return take(state, found)


def take(state, found):
    added = sum(1 for key in found if key not in state['brokers'])
    for key, broker in found.items():
        state['brokers'][key] = {**state['brokers'].get(key, {}), **broker}
    return {'in_registry': len(found), 'new': added, 'with_email': sum(1 for b in found.values() if b['email'])}


def load_brokers(state):
    """Get the broker list: live from every registry, with the copy shipped with this tool standing in for any
    registry that cannot be reached."""
    shipped = json.loads(BUNDLED_LIST.read_text(encoding='utf-8')) if BUNDLED_LIST.exists() else {}
    data, problems = gather(shipped, conditional=False)
    found = usable(data['brokers'])
    if not found:
        raise Oops('Could not reach any registry and there is no bundled list. Check your connection and try again.')
    live = [source for source in SOURCES if source['url']]
    where = 'the live registry and the list shipped with this tool' if len(problems) < len(live) else 'the list shipped with this tool'
    return {**take(state, found), 'from': where}


def add_broker(state, name, email, website=''):
    name, email = ' '.join(str(name).split()), clean_email(email)
    if not name or not email:
        raise Oops('A broker needs a name and a privacy email address.')
    key = 'me-' + secrets.token_hex(4)
    state['brokers'][key] = {'name': name, 'email': email, 'website': str(website).strip(), 'rights_url': '', 'source': 'added by you'}
    return key


# ---------------------------------------------------------------- the letters


def details(profile):
    return ['  Full legal name: ' + profile['name'], '  Email address: ' + profile['email']] + ['  City and region (no street address): ' + where(profile)] * bool(where(profile))


def where(profile):
    return ', '.join(part for part in (profile['city'], profile['region'], profile['country']) if part)


def first_letter(profile, broker, law, ref):
    subject = LAW_NAME[law] + ' from ' + profile['name'] + ' [' + ref + ']'
    body = '\n'.join([
        'To the privacy contact at ' + broker['name'] + ',',
        '',
        'I am ' + profile['name'] + '. This is my own request about my own personal information.',
        '',
        LAW_TEXT[law],
        '',
        'I am not pointing to one listing. Please locate only records matching me and the details below; do not affect people who share my name. Delete matching personal information, opt me out of its sale or sharing, and keep only the minimum suppression record needed to prevent re-listing. If you hold no matching record, please confirm that.',
        '',
        'Details, provided only so that you can locate my records:',
        *details(profile),
        '',
        'Please reply to this address with confirmation of the action taken and the date it was completed, or the specific reason any part of this request is declined. Please do not use these details for any purpose other than processing this request.',
        '',
        'Reference ' + ref + '.',
        '',
        profile['name'],
    ])
    return subject, body


def reminder_letter(profile, broker, law, request):
    return 'Re: ' + request['subject'], '\n'.join([
        'To the privacy contact at ' + broker['name'] + ',',
        '',
        'I sent you a privacy request on ' + day(request['sent_at']) + ' (reference ' + request['ref'] + '). That was ' + str(max(1, (now() - request['sent_at']) // DAY)) + ' days ago and I have no answer.',
        '',
        'Please act on it now and confirm the action taken and the date, or state the specific reason any part is declined. If you hold no record matching the details in that request, a reply saying so settles it.',
        '',
        profile['name'],
    ])


ANSWERS = {
    'use_form': 'You have directed this request to your web form or portal. I made it in writing to this address, which you publish for privacy matters. Please act on it using the details in my original request and the information you hold. If you will not accept a request by email, reply stating the legal requirement you rely on.',
    'wants_id': 'You have asked for an identity document. I do not agree to send one. Please verify this request from the details already provided and the information you hold. At the least, act on it as a request to stop publishing, selling and sharing the personal information that matches those details; that part discloses nothing to the requester. If you will not act without a document, reply stating the specific legal requirement you rely on.',
    'wants_details': 'You have asked for more details. The details in my original request are the ones I am providing. Please act on them, or reply stating which single further detail is strictly necessary and why.',
    'refused': 'You have declined this request. Please act on it at least as a request to stop publishing, selling and sharing the personal information that matches the details I provided. If you will not, reply stating the specific legal requirement you rely on.',
}
CCPA_OPT_OUT = 'California\'s regulations (11 CCR section 7026) do not allow a business to require a verifiable consumer request for an opt-out of sale or sharing.'


def answer_letter(profile, broker, law, request, kind):
    parts = ['To the privacy contact at ' + broker['name'] + ',', '', 'This concerns my privacy request ' + request['ref'] + ' of ' + day(request['sent_at']) + '.', '', ANSWERS[kind]]
    if law == 'ccpa' and kind in ('wants_id', 'refused'):
        parts += ['', CCPA_OPT_OUT]
    return 'Re: ' + request['subject'], '\n'.join(parts + ['', profile['name']])


def final_notice(profile, broker, law, request):
    regulator = REGULATOR[law][0]
    if not regulator:
        raise Oops('No privacy law backs this request, so there is nobody to complain to. Skip this one.')
    deadline = day(now() + NOTICE_DAYS * DAY)
    return 'Re: ' + request['subject'], '\n'.join([
        'To the privacy contact at ' + broker['name'] + ',',
        '',
        'This concerns my privacy request ' + request['ref'] + ' of ' + day(request['sent_at']) + '. You have declined it again.',
        '',
        'I would like to settle this with you directly. Please act on the request, or reply with a way to verify it that I can complete by email, by ' + deadline + '.',
        '',
        'If the request is still refused after that date, I will file a complaint about this refusal with ' + regulator + ' and will include this request and your replies.',
        '',
        'If you hold no record matching the details I provided, a reply saying so settles this request.',
        '',
        profile['name'],
    ])


# ---------------------------------------------------------------- sending

# You do not need to pay for an email service. A free mailbox sends these letters fine.
# These are the sending servers of the common free providers; the tool picks one from your address.
FREE_MAIL = {'gmail.com': 'smtp.gmail.com', 'googlemail.com': 'smtp.gmail.com', 'outlook.com': 'smtp-mail.outlook.com', 'hotmail.com': 'smtp-mail.outlook.com',
             'live.com': 'smtp-mail.outlook.com', 'yahoo.com': 'smtp.mail.yahoo.com', 'icloud.com': 'smtp.mail.me.com', 'me.com': 'smtp.mail.me.com'}
SMTP_HELP = ('Sending from here uses your own free mailbox. Create an "app password" in your email account\'s security settings '
             '(the provider needs two-step sign-in turned on first), then run "send" and paste it when asked. It is never saved. '
             'For a provider this tool does not know, set FDB_SMTP_HOST. No app password? Use the send page instead; it needs nothing.')


def smtp_host(profile):
    return os.environ.get('FDB_SMTP_HOST') or FREE_MAIL.get(profile['email'].rsplit('@', 1)[-1].lower(), '')


# The copy buttons. Browsers differ: the modern clipboard can be missing or refused, so there is an older method
# behind it, and if both fail the button says so and the letter is right there to select by hand.
COPY_SCRIPT = ('<script>document.addEventListener("click",function(e){var b=e.target,v=b.getAttribute&&b.getAttribute("data-copy");if(v===null||v===undefined)return;'
               'function done(){b.textContent="Copied"}'
               'function old(){var t=document.createElement("textarea");t.value=v;t.setAttribute("readonly","");t.style.position="fixed";t.style.top="0";t.style.opacity="0";'
               'document.body.appendChild(t);t.select();var ok=false;try{ok=document.execCommand("copy")}catch(x){}document.body.removeChild(t);'
               'if(ok)done();else b.textContent="Could not copy. Select the text below."}'
               'if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(v).then(done,old);else old();});</script>')


def gmail_link(to, subject, body):
    return 'https://mail.google.com/mail/?view=cm&fs=1&to=' + urllib.parse.quote(to) + '&su=' + urllib.parse.quote(subject) + '&body=' + urllib.parse.quote(body)


def unsent(state):
    """Every email waiting to go: first letters, then answers, reminders and final notices."""
    items = [{'ref': r['ref'], 'label': 'request', 'to': state['brokers'][r['broker']]['email'], 'subject': r['subject'], 'body': r['body'], 'broker': state['brokers'][r['broker']]['name']}
             for r in state['requests'].values() if r['state'] == 'draft' and r.get('via') != 'form']
    return items + [dict(item, broker=state['brokers'][state['requests'][item['ref']]['broker']]['name']) for item in state.get('mail', []) if item['ref'] in state['requests']]


def write_send_page(state):
    """One page with every unsent email: a button that opens it in your mail program, and copy buttons if that fails.
    Free, works with any mailbox, needs no password and no setup."""
    cards, e = [], html.escape
    for item in unsent(state):
        to, subject, body = item['to'], item['subject'], item['body']
        link, paste = mail_link(to, subject, body)
        kind = '' if item['label'] == 'request' else ' <small>' + e(item['label'].replace('-', ' ').replace('_', ' ')) + '</small>'
        cards.append('<section><h2>' + e(item['broker']) + ' <small>' + e(item['ref']) + '</small>' + kind + '</h2>'
                     '<p><a class="go" href="' + e(link) + '">Open in my mail app</a> '
                     '<a class="go" href="' + e(gmail_link(to, subject, body)) + '" target="_blank" rel="noopener">Open in Gmail</a></p>'
                     + ('<p><strong>This letter is too long for some mail apps to open in one go.</strong> If it opens empty, press Copy letter and paste it in.</p>' if paste else '') +
                     '<p>Or copy and paste: <button data-copy="' + e(to) + '">Copy address</button> <button data-copy="' + e(subject) + '">Copy subject</button> '
                     '<button data-copy="' + e(body) + '">Copy letter</button></p><pre>' + e(body) + '</pre></section>')
    return write_page('send.html', 'Letters to send', 'letters to send', 'Each button opens a ready letter in your own email. Press send, then come back for the next one. '
                      'When you are done, run <code>sent all</code> so the clock starts.', cards)


PAGE = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title>'
        '<style>html{background:#fff;color-scheme:light}body{background:#fff;color:#000;font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem}section{border-top:2px solid #000;padding:1rem 0}'
        'pre{white-space:pre-wrap;background:#f4f4f4;padding:1rem}small{font-weight:400;color:#555}.go,button{display:inline-block;margin:.2rem .3rem .2rem 0;padding:.5rem .8rem;'
        'border:2px solid #000;background:#fff;color:#000;text-decoration:none;font:inherit;cursor:pointer}.go{background:#000;color:#fff}</style><h1>%d %s</h1><p>%s</p>%s')


def outbox(name):
    (home() / 'outbox').mkdir(exist_ok=True)
    return home() / 'outbox' / name


def write_page(name, title, heading, intro, cards):
    outbox(name).write_text(PAGE % (title, len(cards), heading, intro, ''.join(cards)) + COPY_SCRIPT, encoding='utf-8')
    return str(outbox(name))


def make_email(profile, to, subject, body):
    message = EmailMessage()
    message['From'], message['To'], message['Subject'] = profile['email'], to, subject
    message.set_content(body)
    return message


def write_draft(profile, to, subject, body, ref, label):
    outbox(ref + '-' + label + '.eml').write_bytes(bytes(make_email(profile, to, subject, body)))
    return str(outbox(ref + '-' + label + '.eml'))


def mailto(to, subject, body):
    return 'mailto:' + urllib.parse.quote(to) + '?subject=' + urllib.parse.quote(subject) + '&body=' + urllib.parse.quote(body)


MAILTO_LIMIT = 1900  # some mail programs cut a longer link short, and a letter cut in half is worse than none


def mail_link(to, subject, body):
    """The link behind "Open in my mail app". Returns it and whether the letter has to be pasted in by hand."""
    full = mailto(to, subject, body)
    if len(full) <= MAILTO_LIMIT:
        return full, False
    return 'mailto:' + urllib.parse.quote(to) + '?subject=' + urllib.parse.quote(subject), True


def smtp_send(profile, items, password=None, sent=lambda item: None):
    """Send through your own mailbox. The password is read from the environment or asked for, and never saved.
    The connection is encrypted and the server's identity is checked before the password is given.
    `sent` is called after each email the server accepts, so a failure half way never causes a second copy."""
    if not smtp_host(profile):
        raise Oops(SMTP_HELP)
    host, port = smtp_host(profile), int(os.environ.get('FDB_SMTP_PORT') or 587)
    with smtplib.SMTP(host, port, timeout=60) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(os.environ.get('FDB_SMTP_USER') or profile['email'], password or os.environ.get('FDB_SMTP_PASS') or '')
        for number, item in enumerate(items):
            if number:
                time.sleep(2)  # no need to look like the spammers we are writing to
            server.send_message(make_email(profile, item['to'], item['subject'], item['body']))
            sent(item)


# ---------------------------------------------------------------- the work


def new_ref():
    return 'req-' + secrets.token_hex(4)


def log(request, text):
    request.setdefault('log', []).append(day(now()) + '  ' + text)


def find_brokers(state, text='', limit=50):
    """Look through the list by name, so you can see who is on it before you write to anyone."""
    asked, skipped, want = {r['broker'] for r in state['requests'].values()}, set(state.get('skip', [])), str(text).strip().lower()
    found = [{'name': b['name'], 'email': b.get('email', ''), 'website': b.get('website', ''), 'from': b.get('source', ''),
              'status': 'skipped by you' if key in skipped else 'already asked' if key in asked else 'not asked yet'}
             for key, b in sorted(state['brokers'].items(), key=lambda item: item[1]['name'].lower()) if want in b['name'].lower()]
    return {'matching': len(found), 'brokers': found[:limit]}


def skip_brokers(state, text, undo=False):
    """Leave brokers out. You decide who gets a letter: a broker you skip is never written to, and a letter
    already written for it but not yet sent is thrown away."""
    want = str(text).strip().lower()
    if not want:
        raise Oops('Say which brokers: a word from their name.')
    keys = [key for key, b in state['brokers'].items() if want in b['name'].lower()]
    skipped = set(state.get('skip', []))
    skipped = skipped - set(keys) if undo else skipped | set(keys)
    state['skip'] = sorted(skipped)
    if not undo:
        for ref in [ref for ref, r in state['requests'].items() if r['broker'] in keys and r['state'] == 'draft' and not r['sent_at']]:
            del state['requests'][ref]
    return {'brokers': sorted(state['brokers'][key]['name'] for key in keys), 'skipped_now': not undo}


def prepare(state, limit=20, match='', forms=False):
    """Write first letters for brokers that have an email address and no request yet. `match` keeps it to
    brokers whose name contains that text. With `forms`, it is the brokers with no email address instead."""
    profile, law = need_profile(state), law_of(state)
    # A broker that said "done" or "nothing here" is asked again after AGAIN_DAYS: they buy the same data again. No law sets the number.
    asked = {r['broker'] for r in state['requests'].values() if forms or r['state'] not in ('done', 'no_record') or r['sent_at'] > now() - AGAIN_DAYS * DAY}
    skipped, want, made = set(state.get('skip', [])), str(match or '').strip().lower(), []
    for key, broker in sorted(state['brokers'].items(), key=lambda item: item[1]['name'].lower()):
        if len(made) >= limit:
            break
        if key in asked or key in skipped or want not in broker['name'].lower() or (broker.get('email') or not form_url(broker) if forms else not broker.get('email')):
            continue
        ref = new_ref()
        subject, body = (LAW_NAME[law] + ' [' + ref + ']', form_text(profile, law, ref)) if forms else first_letter(profile, broker, law, ref)
        request = {'ref': ref, 'broker': key, 'law': law, 'state': 'draft', 'subject': subject, 'body': body, 'sent_at': 0, 'due_at': 0, 'answers': []}
        request.update({'via': 'form'} if forms else {'draft': write_draft(profile, broker['email'], subject, body, ref, 'request')})
        log(request, 'Form sheet written.' if forms else 'Letter written.')
        state['requests'][ref] = request
        made.append(request)
    return made


def web_address(value):
    """A plain web address, or nothing. Brokers often register theirs as "www.example.com" with no https:// in front."""
    url = str(value or '').strip().rstrip(';,.')
    if url and not re.match(r'https?://', url, re.I) and re.match(r'[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}(?:[/?#][^\s"<>]*)?$', url):
        url = 'https://' + url
    return url if re.match(r'https?://[^\s"<>]+$', url, re.I) else ''


def form_url(broker):
    """Where the broker takes privacy requests: its registered privacy page, or failing that its website."""
    return web_address(broker.get('rights_url')) or web_address(broker.get('website'))


def form_text(profile, law, ref):
    return LAW_TEXT[law] + ' Please locate only records matching me; do not affect people who share my name. Reference ' + ref + '. ' + profile['name']


def form_waiting(request):
    return request['state'] == 'form' or request['state'] == 'draft' and request.get('via') == 'form'


def prepare_forms(state, limit=10):
    """For brokers with no email address: the form you fill in yourself, with everything ready to paste.
    You are the person, so you press submit and you do any CAPTCHA. Nothing is bypassed and nothing is submitted for you."""
    return prepare(state, limit, forms=True)


def write_forms_page(state):
    profile, cards, e = need_profile(state), [], html.escape
    for request in state['requests'].values():
        if not form_waiting(request):
            continue
        broker = state['brokers'][request['broker']]
        cards.append('<section><h2>' + e(broker['name']) + ' <small>' + e(request['ref']) + '</small></h2>'
                     '<p><a class="go" href="' + e(form_url(broker)) + '" target="_blank" rel="noopener noreferrer">Open their form</a></p>'
                     '<p>Paste as needed: <button data-copy="' + e(profile['name']) + '">Copy name</button> <button data-copy="' + e(profile['email']) + '">Copy email</button> '
                     + ('<button data-copy="' + e(where(profile)) + '">Copy city</button> ' if where(profile) else '') + '<button data-copy="' + e(request['body']) + '">Copy request text</button></p>'
                     '<pre>' + e(request['body']) + '</pre></section>')
    return write_page('forms.html', 'Forms to fill in', 'brokers that hide behind a form', 'These ones will not act on an email, so you fill in their form. Open it, pick "delete" and "do not sell or share", '
                      'paste your details, do their CAPTCHA, submit. Give them only what is on this page. If a form demands a photo of your ID, close the tab '
                      'and run <code>noid</code> with that reference: the request carries on without the form. '
                      'When you are done, run <code>sent</code> with the references you submitted.', cards)


def queue(state, profile, broker, ref, label, subject, body):
    """Put a follow-up email in the queue and write it as a draft file too."""
    state.setdefault('mail', []).append({'ref': ref, 'label': label, 'to': broker['email'], 'subject': subject, 'body': body})
    return write_draft(profile, broker['email'], subject, body, ref, label)


def mark_sent(state, refs):
    done = []
    state['mail'] = [item for item in state.get('mail', []) if item['ref'] not in refs]
    for ref in refs:
        request = state['requests'].get(ref)
        if request and request['state'] in ('draft', 'form'):
            words = 'Form submitted' if form_waiting(request) else 'Sent'
            request.update(state='sent', sent_at=request['sent_at'] or now(), due_at=now() + DEADLINE_DAYS[request['law']] * DAY)
            log(request, words + '. They have until ' + day(request['due_at']) + '.')
            done.append(ref)
    return done


def send_all(state, limit=20, password=None):
    """Send what is waiting: follow-ups first, then first letters, up to the limit. Each one is recorded the moment
    the mail server accepts it and the record is saved, so stopping half way never sends anything twice."""
    profile = need_profile(state)
    items, done = unsent(state), []
    items = [i for i in items if i['label'] != 'request'] + [i for i in items if i['label'] == 'request'][:limit]

    def accepted(item):
        if item['label'] == 'request':
            mark_sent(state, [item['ref']])
        else:
            state['mail'] = [m for m in state.get('mail', []) if not (m['ref'] == item['ref'] and m['label'] == item['label'])]
            log(state['requests'][item['ref']], item['label'].replace('-', ' ').replace('_', ' ').capitalize() + ' sent.')
        done.append(item['ref'] + (' (' + item['label'] + ')' if item['label'] != 'request' else ''))
        save(state)

    try:
        smtp_send(profile, items, password, accepted)
    except (OSError, smtplib.SMTPException) as problem:
        if done:
            raise Oops(str(len(done)) + ' sent and recorded, then it stopped: ' + explain(problem) + ' Run "send" again for the rest; nothing will be sent twice.')
        raise
    return done


# What a broker's reply usually means. Checked in this order; the first match wins.
REPLY_RULES = [
    ('bounced', r'\b(?:undeliverable|delivery (?:status notification|has failed|failed|incomplete)|could ?n[o\']t be delivered|address (?:not found|couldn\'?t be found)|mailbox (?:unavailable|not found|is full)|user unknown|no such (?:user|recipient|address)|recipient address rejected|mailer-daemon)\b'),
    ('done', r'\b(?:has|have) been (?:deleted|removed|erased|suppressed|opted out|processed|completed)\b|\b(?:we have|we\'ve) (?:deleted|removed|erased|suppressed|completed|processed|opted you out)\b|\bsuccessfully (?:deleted|removed|opted|processed)\b|\brequest (?:is|has been) complete'),
    ('click_link', r'\b(?:click|follow|open|use) (?:on )?(?:the|this) (?:link|button)\b|\bverification (?:link|code)\b|\b(?:verify|confirm) your e-?mail\b'),
    ('wants_id', r'\b(?:government[- ]issued|photo id|driver\'?s licen[cs]e|passport|identity document|proof of identity|copy of (?:your )?id|notari[sz]ed|power of attorney)\b'),
    ('wants_details', r'\b(?:additional|more|further) (?:information|details|identifiers)\b|\b(?:insufficient|not (?:have )?(?:sufficient|enough)|(?:do|does) not have (?:sufficient|enough)) information\b|\bplease (?:provide|reply with|send)\b[^.!?]{0,80}\b(?:date of birth|address|phone|full name)\b'),
    ('no_record', r'\b(?:found no|did not (?:locate|find)|(?:were|are) unable to (?:locate|find)|could not (?:find|locate)|no records? (?:matching|found|were found))\b[^.!?]{0,120}\b(?:records?|information|data|match)|\b(?:have|has|hold|holds) no (?:records?|data|information)\b|\b(?:do|does) not (?:have|hold) (?:any )?(?:records?|data|personal information)\b[^.!?]{0,60}\b(?:about|on|for|matching|associated)\b'),
    ('moved', r'\b(?:instead|no longer (?:monitored|used|in use|active)|not monitored|new (?:e-?mail )?address|(?:send|direct|forward|submit|resubmit) (?:your|this|the|all|any|such)\b[^.!?]{0,60}\bto)\b[^!?]{0,160}?[\w.+-]+@[\w-]+\.\w'),
    ('use_form', r'\b(?:web ?form|online form|our form|privacy (?:portal|center|request page)|opt[- ]?out (?:form|page|portal)|submit (?:your|the|a) request (?:through|via|using|at|on)|complete (?:the|our) (?:form|portal)|does not (?:accept|process)[^.!?]{0,60}(?:by|via) e-?mail|not monitored)\b|\bto (?:opt[- ]?out|submit (?:a|your) request|exercise your (?:privacy )?rights)\b[^.!?]{0,80}\b(?:visit|go to|use)\b|\bplease visit\b[^.!?]{0,80}\b(?:opt[- ]?out|privacy|request)'),
    ('refused', r'\b(?:unable to (?:process|verify|honou?r|complete|fulfil)|cannot (?:process|verify|honou?r|complete|fulfil)|can\'?t (?:process|verify)|(?:is|has been|are) (?:denied|declined|rejected)|we (?:deny|decline)|not (?:subject to|covered by|applicable)|does not apply|exempt)\b|\b(?:have|has) not (?:been )?(?:deleted|removed|erased|processed)\b|\bcannot be (?:deleted|removed|erased|processed|completed|honou?red)\b'),
    ('received', r'\b(?:received|receipt of|acknowledge|thank you for (?:contacting|your)|ticket|case (?:number|id)|will (?:respond|review|process|get back)|will be (?:deleted|removed|erased|processed|completed))\b'
                 r'|\b(?:not (?:been )?(?:completed|processed|finished) yet|(?:stays?|remains?) open|on file|in progress|being processed|will (?:email|contact|notify|update) you|will be in touch)\b'),
]

MEANING = {
    'done': 'They say it is done. A broker saying so is a claim, not proof, but it is the answer you wanted.',
    'no_record': 'They say they hold nothing on you. Fine. One down.',
    'click_link': 'They want you to click a link to confirm. This tool never clicks links for you. Look at where the link goes: if it is on the broker\'s own website, click it yourself. If it goes anywhere else, ignore it.',
    'wants_id': 'They want your ID. A company that sells personal data wants more personal data. No.',
    'use_form': 'They want you to use their web form. You already asked in writing; they just prefer a form with a CAPTCHA.',
    'wants_details': 'They want more details about you before they will delete the details they have about you.',
    'refused': 'They refused. Answer once; if they refuse again, send the final notice.',
    'received': 'An automatic "we got it". Means nothing yet. The clock keeps running.',
    'bounced': 'Your letter bounced: that address is dead and nothing reached them. Look on their own website for a privacy address or a form.',
    'moved': 'They want the request sent to a different address. I only believe an address on the broker\'s own domain; check any other one on their website before you use it.',
    'unclear': 'I cannot tell what this means. Read it yourself; if they ask for a link click or an ID, do not.',
}


# Words that mention something without saying it happened or is wanted. Each runs to the next comma or the end of the
# sentence, and is removed before the rules named for it are checked.
# "You do not need to fill in the opt-out form" is not a demand to use the form (checked for every rule).
NOT_NEEDED = r'\b(?:(?:do|does|did) not|don\'?t|doesn\'?t|no) need to\b[^.!?;]*'
# "We will write once all actions have been completed" is not done; "after review, it has been deleted" still is.
NOT_YET = r'\b(?:once|when|after|until|as soon as|if)\b[^,.!?;]*'
# "We will be in touch if we need any additional information" is not a request for more details.
IF_WE_NEED = r'\bif (?:we|they|our team) (?:need|require)s?\b[^,.!?;]*'
SET_ASIDE = {'done': NOT_YET, 'wants_details': IF_WE_NEED}


# The tool rewrites its own rules. A reply it cannot read costs you one line: a few words from it and what they meant.
# From then on it reads that reply by itself. learned.json holds the rules people sent in, so everyone's copy gets smarter.
LEARNED = json.loads(Path(__file__).with_name('learned.json').read_text(encoding='utf-8')) if Path(__file__).with_name('learned.json').exists() else []


def teach(state, kind, phrase):
    phrase = ' '.join(str(phrase).lower().split())
    if kind not in MEANING or kind == 'unclear':
        raise Oops('What it meant must be one of: ' + ', '.join(k for k in MEANING if k != 'unclear') + '.')
    if not 2 <= len(phrase.split()) <= 12 or re.search(r'[\d@/]', phrase):
        raise Oops('Give 2 to 12 plain words from the reply. No names, numbers, addresses or links: a rule is meant to be shared.')
    state['learned'] = [rule for rule in state.get('learned', []) if rule[1] != phrase] + [[kind, phrase]]
    return {'rules_you_taught': len(state['learned']), 'share': share(state)}


def share(state):  # what you taught it, with nothing about you in it; post it as an issue and every copy learns it
    return [kind + ': ' + phrase for kind, phrase in state.get('learned', [])]


def classify(text, learned=()):
    flat = re.sub(NOT_NEEDED, ' ', ' '.join(str(text).lower().split()))
    for kind, phrase in list(learned) + LEARNED:
        if kind in MEANING and phrase in flat:
            return kind
    for kind, pattern in REPLY_RULES:
        if re.search(pattern, re.sub(SET_ASIDE[kind], ' ', flat) if kind in SET_ASIDE else flat):
            return kind
    return 'unclear'


def find_ref(state, text):
    """Replies quote the subject line, and the subject line carries the reference."""
    for ref in re.findall(r'req-[0-9a-f]{8}', str(text).lower()):
        if ref in state['requests']:
            return ref
    return ''


def read_reply(state, ref, text):
    """Record a broker's reply and say what to do. Returns the answer to send, if there is one."""
    profile = need_profile(state)
    if not str(text).strip():
        raise Oops('That reply is empty. Paste what the broker wrote.')
    ref = str(ref or '').strip() or find_ref(state, text)
    if not ref:
        raise Oops('I cannot tell which request this reply is about. Give the reference: it is in the subject line and looks like req-1a2b3c4d.')
    request = state['requests'].get(ref)
    if not request:
        raise Oops('No request with reference ' + ref + '.')
    if request['state'] == 'draft':
        raise Oops('That letter has not been sent yet.')
    broker, kind = state['brokers'][request['broker']], classify(text, state.get('learned', ()))
    result = {'ref': ref, 'broker': broker['name'], 'kind': kind, 'meaning': MEANING[kind], 'answer': None}
    log(request, 'Reply: ' + kind + '.')
    if kind == 'unclear':
        result['meaning'] += ' Then teach me, so I read the next one myself: teach KIND "a few words from it". KIND is one of: ' + ', '.join(k for k in MEANING if k != 'unclear') + '.'
    moved = next((a for a in re.findall(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+', str(text).lower()) if kind == 'moved' and a != broker['email'] and a.split('@')[1] == broker['email'].split('@')[-1]), '')
    if moved:  # only an address on the broker's own domain is believed; anything else could be anyone
        broker['email'] = moved
        request.update(state='draft', draft=write_draft(profile, moved, request['subject'], request['body'], ref, 'request'))
        result['meaning'] += ' The letter now goes to ' + moved + '. It is on your send page.'
    elif kind in ('use_form', 'bounced') and form_url(broker):
        # You are right here, so filling their form beats arguing about it by email.
        request.update(state='form', via='form', body=form_text(profile, request['law'], ref))
        result['form'] = form_url(broker)
        result['meaning'] += ' It is on your forms page now: run "forms".'
    elif kind in ('done', 'no_record', 'bounced'):
        request['state'] = kind
    elif kind == 'received':
        request['state'] = 'acknowledged' if request['state'] == 'sent' else request['state']
    elif kind in ANSWERS:
        if kind in request['answers'] or 'final_notice' in request['answers']:
            # They said it twice. One final notice, then the regulator.
            request['state'] = 'refused'
            if 'final_notice' in request['answers']:
                result['meaning'] += ' You already sent the final notice. See "chase" for where to complain.'
            elif REGULATOR[request['law']][0]:
                subject, body = final_notice(profile, broker, request['law'], request)
                request['answers'].append('final_notice')
                request['notice_due'] = now() + NOTICE_DAYS * DAY
                result['answer'] = {'to': broker['email'], 'subject': subject, 'body': body, 'draft': queue(state, profile, broker, ref, 'final-notice', subject, body), 'kind': 'final_notice'}
                log(request, 'Final notice written. They have ' + str(NOTICE_DAYS) + ' days.')
            else:
                result['meaning'] += ' No law backs this request, so it ends here.'
        else:
            subject, body = answer_letter(profile, broker, request['law'], request, kind)
            request['answers'].append(kind)
            request['state'] = 'answered'
            result['answer'] = {'to': broker['email'], 'subject': subject, 'body': body, 'draft': queue(state, profile, broker, ref, 'answer-' + kind, subject, body), 'kind': kind}
            log(request, 'Answer written (' + kind + ').')
    return result


def imap_host(profile):  # the reading server is the sending server's twin: smtp.example.com, imap.example.com
    host = os.environ.get('FDB_IMAP_HOST') or smtp_host(profile).replace('smtp', 'imap', 1)
    if not host:
        raise Oops('I do not know your provider\'s mail server. Set FDB_IMAP_HOST, or paste replies with "reply".')
    return host


def inbox(state, password=None, connect=imaplib.IMAP4_SSL):
    """Read the brokers' replies straight from your own mailbox, the way a mail app does: this computer to your
    provider, nobody in between. It only looks, it changes nothing there, and it asks for nothing but mail whose
    subject carries one of your references. A reply is acted on only when it comes from the broker's own mail domain."""
    profile, out, seen = need_profile(state), [], set(state.get('read', []))
    host = imap_host(profile)
    try:
        with connect(host, ssl_context=ssl.create_default_context()) as box:
            box.login(os.environ.get('FDB_SMTP_USER') or profile['email'], password or os.environ.get('FDB_SMTP_PASS') or '')
            box.select('INBOX', readonly=True)
            for ref, request in list(state['requests'].items()):
                for number in (box.search(None, 'SUBJECT', '"' + ref + '"')[1][0] or b'').split() if request['sent_at'] and request['state'] != 'draft' else []:
                    message = email.message_from_bytes(box.fetch(number, '(BODY.PEEK[])')[1][0][1], policy=email.policy.default)
                    sender, ident, part, broker = clean_email(message['From']), str(message['Message-ID']), message.get_body(('plain', 'html')), state['brokers'][request['broker']]
                    if ident in seen or sender == profile['email'].lower():
                        continue
                    seen.add(ident)
                    body = part.get_content() if part else ''
                    text = str(message['Subject']) + '\n' + (re.sub(r'<[^>]+>', ' ', body) if part and part.get_content_subtype() == 'html' else body)
                    out.append(read_reply(state, ref, text) if sender.split('@')[-1] == broker['email'].split('@')[-1] or classify(text) == 'bounced' else
                               {'ref': ref, 'broker': broker['name'], 'kind': 'stranger', 'answer': None, 'meaning': 'A reply came from ' + sender + ', which is not the broker\'s own address, so I changed nothing. Read it yourself; if it is theirs, paste it with "reply".'})
    except imaplib.IMAP4.error as problem:
        raise Oops('Your mailbox said no (' + str(problem)[:120] + '). It wants an app password, and mail-app access (IMAP) switched on in your email settings.')
    state['read'] = sorted(seen)
    return out


ID_WALL = 'Your web form will not accept this request without a government identity document, which I do not agree to provide. This letter is my request, made in writing to the address you publish for privacy matters.'
WALL_WHY = 'takes requests only through a form that demands an identity document'


def form_demands_id(state, ref):
    """Their form wants a photo of your ID. Closing the tab is right, and it is not the end: the request goes on
    in writing, and if they still refuse it goes to the regulator. Refusing to hand over ID is not giving up."""
    profile = need_profile(state)
    request = state['requests'].get(str(ref or '').strip())
    if not request:
        raise Oops('No request with reference ' + str(ref) + '.')
    if not (form_waiting(request) or request.get('via') == 'form'):
        raise Oops('That request is not waiting on a form.')
    broker = state['brokers'][request['broker']]
    log(request, 'Their form demands an identity document.')
    if request['sent_at'] and broker.get('email'):
        # They already have the request by email and sent you to the form: answer the demand as any ID demand.
        return read_reply(state, request['ref'], 'Your form requires a government-issued photo ID.')
    result = {'ref': request['ref'], 'broker': broker['name'], 'kind': 'wants_id', 'answer': None}
    if broker.get('email'):
        subject, body = first_letter(profile, broker, request['law'], request['ref'])
        body = body.replace('\n\nReference ' + request['ref'] + '.', '\n\n' + ID_WALL + '\n\nReference ' + request['ref'] + '.')
        request.pop('via', None)
        request.update(state='draft', subject=subject, body=body, draft=write_draft(profile, broker['email'], subject, body, request['ref'], 'request'))
        result['meaning'] = 'You did the right thing. Your request now goes to them by email instead, and says why. It is on your send page.'
        return result
    request['state'] = 'id_wall'
    regulator, how = REGULATOR[request['law']]
    result['meaning'] = ('This broker publishes no email address and its only form demands your ID. ' +
                         ('That is a refusal. Complain to ' + regulator + '. ' + how if regulator else 'No privacy law backs this request, so it ends here.'))
    return result


SILENCE_DAYS, AGAIN_DAYS = 14, 180


def chase(state):
    """Reminders for brokers that went quiet, and where to complain about the ones that refused twice or never answered."""
    profile, out = need_profile(state), {'reminders': [], 'complaints': []}
    for request in state['requests'].values():
        broker, waiting = state['brokers'][request['broker']], request['state'] in ('sent', 'acknowledged', 'answered')
        if waiting and request['due_at'] and request['due_at'] < now() and not request.get('reminded'):
            subject, body = reminder_letter(profile, broker, request['law'], request)
            request['reminded'] = now()
            log(request, 'Reminder written.')
            out['reminders'].append({'ref': request['ref'], 'broker': broker['name'], 'to': broker['email'], 'subject': subject, 'body': body,
                                     'draft': queue(state, profile, broker, request['ref'], 'reminder', subject, body)})
            continue
        regulator, how = REGULATOR[request['law']]
        why = ('refused twice and ignored the final notice' if 'final_notice' in request['answers'] and request.get('notice_due', 0) < now() and request['state'] not in ('done', 'no_record') else
               'never answered, even after a reminder' if waiting and request.get('reminded') and request['reminded'] + SILENCE_DAYS * DAY < now() else WALL_WHY if request['state'] == 'id_wall' else '')
        if why and regulator:
            out['complaints'].append({'ref': request['ref'], 'broker': broker['name'], 'why': why, 'regulator': regulator, 'how': how,
                                      'include': 'A screenshot of the form demanding the document, and the address of that page.' if why == WALL_WHY else
                                      'Your original request (' + day(request['sent_at']) + '), anything the broker wrote back, and your reminder or final notice.'})
    return out


def routine(state, limit=20, send=False, read=False):
    """Everything that needs no decision from you, in one go: refresh the broker list, write the next letters,
    write reminders for brokers that are late, rebuild your pages, and send if you asked for that. Safe to run
    as often as you like and built to be put on a schedule; it never writes to the same broker twice."""
    need_profile(state)
    done = {'new_brokers': 0, 'letters_written': 0, 'forms_waiting': 0, 'reminders_written': 0, 'complaints_ready': 0, 'sent': [], 'notes': []}
    try:
        done['new_brokers'] = load_brokers(state)['new']
    except Exception as problem:  # the list you already have is still good
        done['notes'].append('The broker list was not refreshed: ' + (str(problem) or type(problem).__name__)[:120])
    done['letters_written'] = len(prepare(state, limit))
    prepare_forms(state, limit)
    done['forms_waiting'] = sum(1 for r in state['requests'].values() if form_waiting(r))
    if read:
        try:
            done['notes'].append(str(len(inbox(state))) + ' new replies read from your mailbox.')
        except (Oops, OSError) as problem:
            done['notes'].append('Your mailbox was not read: ' + str(problem)[:200])
    chased = chase(state)
    done['reminders_written'], done['complaints_ready'] = len(chased['reminders']), len(chased['complaints'])
    done['waiting_to_send'], done['send_page'], done['forms_page'] = len(unsent(state)), write_send_page(state), write_forms_page(state)
    if send:
        if not os.environ.get('FDB_SMTP_PASS'):
            done['notes'].append('Nothing was sent: set FDB_SMTP_PASS to your app password to send on a schedule, or open the send page.')
        else:
            done['sent'] = send_all(state, max(limit, 20))  # letters already waiting go out whatever today's limit for new ones
            done['waiting_to_send'] = len(unsent(state))
    return done


STATE_WORDS = {'id_wall': 'their only form demands ID', 'form': 'they want their form filled in', 'draft': 'letter written, not sent', 'sent': 'sent, waiting', 'acknowledged': 'they said "received"', 'answered': 'you answered their excuse',
               'refused': 'refused twice', 'bounced': 'their address is dead', 'done': 'they say deleted', 'no_record': 'they hold nothing'}


def status(state):
    counts = {}
    for request in state['requests'].values():
        counts[request['state']] = counts.get(request['state'], 0) + 1
    brokers = state['brokers']
    return {'you': (state['profile'] or {}).get('name', ''), 'brokers_known': len(brokers), 'brokers_with_email': sum(1 for b in brokers.values() if b.get('email')),
            'requests': len(state['requests']), 'by_state': {STATE_WORDS[k]: v for k, v in sorted(counts.items())},
            'overdue': sum(1 for r in state['requests'].values() if r['state'] in ('sent', 'acknowledged', 'answered') and r['due_at'] and r['due_at'] < now()),
            'note': 'A sent letter is not a deletion. "Done" means the broker said so.'}


# ---------------------------------------------------------------- MCP, so an AI can do the boring part

TOOLS = [
    ('fdb_status', 'Where things stand: who the person is, how many brokers are known, how many letters are out and what came back.', {}, []),
    ('fdb_setup', 'Save who the person is. Ask them for every value; never guess a name, email or place. place is one of: ' + ', '.join(PLACES) + '.',
     {'name': 'Full legal name', 'email': 'Their own email address; brokers reply to it', 'city': 'City', 'region': 'State or province', 'country': 'Country', 'place': 'Which law applies'},
     ['name', 'email', 'place']),
    ('fdb_import_brokers', 'Download California\'s public data broker registry (several hundred brokers with privacy contacts).', {}, []),
    ('fdb_add_broker', 'Add one broker by hand. Use only a privacy email address published on the broker\'s own site.', {'name': 'Broker name', 'email': 'Privacy email address', 'website': 'Website'}, ['name', 'email']),
    ('fdb_find_brokers', 'Look through the broker list by name. Use it to show the person who is on the list before any letter is written.', {'text': 'Part of a broker name; leave out to see the first 50'}, []),
    ('fdb_skip_brokers', 'Leave out every broker whose name contains the text, so it is never written to. The person decides who gets a letter. Set undo to "yes" to put them back.', {'text': 'Part of a broker name', 'undo': '"yes" to stop skipping them'}, ['text']),
    ('fdb_write_letters', 'Write deletion and opt-out letters for brokers not yet asked. Returns each letter and its draft file. Sends nothing. Each letter gives the broker the person\'s name, email address and city, and nothing else.', {'limit': 'How many (default 20)', 'match': 'Only brokers whose name contains this text'}, []),
    ('fdb_forms', 'For brokers that answered "use our form", or publish no email address: returns each broker\'s own form page and the exact values to enter. If you have a browser tool you may open the page and fill the fields while the person watches; the person completes any CAPTCHA and presses submit themselves. Enter only the values given. Never upload an identity document. Afterwards call fdb_mark_sent.', {'limit': 'How many (default 10)'}, []),
    ('fdb_form_demands_id', 'Call this when a broker\'s form will not go through without a photo of the person\'s identity document. Never upload one. The request carries on by email, or the tool says where to complain.', {'ref': 'Request reference'}, ['ref']),
    ('fdb_send', 'Send the written letters through the person\'s own free mailbox. Needs FDB_SMTP_PASS set to an app password. Ask the person before sending. Without it, give them the send page from fdb_write_letters.', {'limit': 'How many (default 20)'}, []),
    ('fdb_mark_sent', 'Record that the person sent these letters themselves.', {'refs': 'Comma-separated references, or "all"'}, ['refs']),
    ('fdb_read_reply', 'Give it a broker\'s reply text. It says what the reply means and writes the answer to send, if one is needed. Never click links in a reply and never send an identity document.', {'ref': 'Request reference, like req-1a2b3c4d. Leave out if the reply text contains it.', 'text': 'The reply text, including its subject line'}, ['text']),
    ('fdb_routine', 'Do everything that needs no decision in one call: refresh the broker list, write the next letters, write reminders for late brokers and rebuild the pages. Sends nothing. Good for a regular check-in.', {'limit': 'How many new letters (default 20)'}, []),
    ('fdb_teach', 'When fdb_read_reply says "unclear" and you can see what the reply means, teach the tool so it reads the next one itself. Give the meaning and 2 to 12 plain words copied from the reply that carry it. Never include a name, number, address or link. Then read the reply again.', {'kind': 'One of: ' + ', '.join(k for k in MEANING if k != 'unclear'), 'phrase': 'A few words from the reply'}, ['kind', 'phrase']),
    ('fdb_more', 'Free official steps beyond data brokers for where the person lives: do-not-call lists, credit freezes and the like. Each was read on the official page it links to. Tell the person about them; do none of them yourself.', {}, []),
    ('fdb_chase', 'Write reminders for brokers past their deadline, and list where to complain about the ones that refused twice.', {}, []),
]


def record_sent(state, refs):
    if refs == ['all']:
        refs, state['mail'] = [r['ref'] for r in state['requests'].values() if r['state'] == 'draft' and r.get('via') != 'form'], []
    return mark_sent(state, refs)


def call_tool(name, args):
    state = load()
    simple = {
        'fdb_status': lambda: status(state), 'fdb_import_brokers': lambda: {**load_brokers(state), 'credit': REGISTRY_CREDIT},
        'fdb_add_broker': lambda: {'added': add_broker(state, args.get('name', ''), args.get('email', ''), args.get('website', ''))},
        'fdb_find_brokers': lambda: find_brokers(state, args.get('text', '')), 'fdb_form_demands_id': lambda: form_demands_id(state, args.get('ref', '')),
        'fdb_skip_brokers': lambda: skip_brokers(state, args.get('text', ''), str(args.get('undo', '')).strip().lower() in ('yes', 'true', '1')),
        'fdb_mark_sent': lambda: {'marked_sent': record_sent(state, [r.strip() for r in str(args.get('refs', '')).split(',') if r.strip()])},
        'fdb_read_reply': lambda: read_reply(state, str(args.get('ref', '')).strip(), args.get('text', '')),
        'fdb_routine': lambda: routine(state, int(args.get('limit') or 20)), 'fdb_chase': lambda: {**chase(state), 'send_page': write_send_page(state)},
        'fdb_more': lambda: {'steps': more(state)}, 'fdb_teach': lambda: teach(state, str(args.get('kind', '')).strip(), args.get('phrase', '')),
    }
    if name in simple:
        out = simple[name]()
    elif name == 'fdb_setup':
        out = dict(set_profile(state, args.get('name', ''), args.get('email', ''), args.get('city', ''), args.get('region', ''), args.get('country', ''), args.get('place', '')))
        if out['place'] == 'california':
            out['tell_the_person'] = DROP_TIP
    elif name == 'fdb_write_letters':
        made = prepare(state, int(args.get('limit') or 20), args.get('match', ''))
        out = {'written': len(made), 'letters': [{'ref': r['ref'], 'to': state['brokers'][r['broker']]['email'], 'broker': state['brokers'][r['broker']]['name'], 'subject': r['subject'], 'draft': r['draft']} for r in made],
               'send_page': write_send_page(state),
               'next': 'The person opens send_page in a browser and clicks one button per letter (free, no setup), then you call fdb_mark_sent. Or use fdb_send if an app password is set.'}
    elif name == 'fdb_forms':
        prepare_forms(state, int(args.get('limit') or 10))
        profile = need_profile(state)
        out = {'forms': [{'ref': r['ref'], 'broker': state['brokers'][r['broker']]['name'], 'form_page': form_url(state['brokers'][r['broker']]),
                          'enter': {'name': profile['name'], 'email': profile['email'], 'city': profile['city'], 'region': profile['region'], 'country': profile['country'], 'request_text': r['body']}}
                         for r in state['requests'].values() if form_waiting(r)],
               'page_for_the_person': write_forms_page(state)}
    elif name == 'fdb_send':
        if not os.environ.get('FDB_SMTP_PASS'):
            raise Oops('Sending from here needs FDB_SMTP_PASS set to an app password, which only the person can create. Without it, give them the send page: ' + write_send_page(state))
        out = {'sent': send_all(state, int(args.get('limit') or 20))}
    else:
        raise Oops('Unknown tool: ' + name)
    save(state)
    return out


def mcp_reply(message):
    """Answer one MCP (JSON-RPC) message. Returns None for notifications."""
    method, ident = message.get('method'), message.get('id')
    if ident is None:
        return None
    if method == 'initialize':
        asked = (message.get('params') or {}).get('protocolVersion')
        result = {'protocolVersion': asked if asked in ('2025-06-18', '2025-03-26', '2024-11-05') else '2024-11-05', 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'fuck_data_brokers', 'version': VERSION},
                  'instructions': 'Helps one person make data brokers delete their information. Start with fdb_status. Ask the person for their details; never invent them. Ask before sending email. Never send identity documents or follow links from broker replies.'}
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = {'tools': [{'name': name, 'description': text, 'inputSchema': {'type': 'object', 'properties': {k: {'type': 'string', 'description': v} for k, v in fields.items()}, 'required': required}} for name, text, fields, required in TOOLS]}
    elif method == 'tools/call':
        params = message.get('params') or {}
        try:
            result = {'content': [{'type': 'text', 'text': json.dumps(call_tool(params.get('name'), params.get('arguments') or {}), indent=1, ensure_ascii=False)}], 'isError': False}
        except Exception as problem:  # the AI gets the reason, the server stays up
            result = {'content': [{'type': 'text', 'text': ('' if isinstance(problem, Oops) else 'It failed: ') + str(problem)}], 'isError': True}
    else:
        return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32601, 'message': 'Unknown method: ' + str(method)}}
    return {'jsonrpc': '2.0', 'id': ident, 'result': result}


def mcp_serve():
    for line in filter(None, map(str.strip, sys.stdin)):
        try:
            answer = mcp_reply(json.loads(line))
        except json.JSONDecodeError:
            answer = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'That was not JSON.'}}
        if answer is not None:
            print(json.dumps(answer), flush=True)


# ---------------------------------------------------------------- manual mode, for humans

BANNER = '''
  f*ck_data_brokers
  Because "we value your privacy" is the funniest thing a data broker ever wrote.
'''


def ask(prompt, default=''):
    answer = input(prompt + (' [' + default + ']' if default else '') + ': ').strip()
    return answer or default


def show(value):
    print(json.dumps(value, indent=1, ensure_ascii=False))


def pick_place(default):
    keys = list(PLACES)
    print('Where do you live? It decides which law your letters quote. One letter can only quote one law.')
    for number, key in enumerate(keys, 1):
        print('   ' + str(number) + '. ' + PLACES[key][0])
    while True:
        answer = ask('Pick a number', str(keys.index(default) + 1 if default in keys else len(keys))).lower()
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return keys[int(answer) - 1]
        for key in keys:
            if answer in (key, PLACES[key][0].lower()):
                return key
        print('Pick one number from the list. If you move around, pick where you live most of the year. Not sure? Pick ' + str(len(keys)) + '.')


def do_setup(state):
    old = dict(state.get('profile') or {})
    print('\nWho are we deleting? This stays on your computer.')
    while True:
        old['name'] = ask('Your full legal name', old.get('name', ''))
        old['email'] = ask('Your email address (brokers reply here)', old.get('email', ''))
        old['city'], old['region'], old['country'] = ask('City', old.get('city', '')), ask('State or province', old.get('region', '')), ask('Country', old.get('country', ''))
        old['place'] = pick_place(old.get('place', ''))
        try:
            set_profile(state, old['name'], old['email'], old['city'], old['region'], old['country'], old['place'])
            break
        except Oops as problem:
            print('\n' + str(problem) + ' Try again; press Enter to keep an answer.\n')
    print('Saved. No street address, no birthday, no ID. They get the minimum.')
    if old['place'] == 'california':
        print('\n' + DROP_TIP)


def do_import(state):
    print('\nFetching the list of companies that registered, in writing, as sellers of people...')
    print(json.dumps(load_brokers(state), indent=1))
    print(REGISTRY_CREDIT)


def do_letters(state, limit=20, match=None):
    if match is None:
        match = ask('\nWrite to every broker, or only names containing a word? (Enter for every broker)')
    made = prepare(state, limit, match)
    if not made:
        print('\nNo new letters to write. Either everyone ' + ('matching "' + match + '" ' if match else '') + 'has been asked, or you have no brokers yet (run "brokers").')
        return
    print('\nEach letter gives the broker three things: your name, your email address and your city. Nothing else.')
    print('\nWrote ' + str(len(made)) + ' letters. Drafts are in ' + str(home() / 'outbox') + '\n')
    for request in made[:5]:
        broker = state['brokers'][request['broker']]
        print('  ' + request['ref'] + '  ' + broker['name'] + '  <' + broker['email'] + '>')
    if len(made) > 5:
        print('  ... and ' + str(len(made) - 5) + ' more.')
    print('\nSending costs nothing. Use the free mailbox you already have. Two ways:')
    print('  1. Open this page and click one button per letter, then run "sent all":')
    print('       ' + write_send_page(state))
    print('  2. Or run "send" and let this tool send them all. ' + SMTP_HELP)
    print('\nTip: a separate free address just for this keeps their replies out of your real inbox.')


def do_forms(state, limit=10):
    made = prepare_forms(state, limit)
    waiting = [r for r in state['requests'].values() if form_waiting(r)]
    if not waiting:
        print('\nNo form-only brokers waiting. Either you did them all, or run "brokers" first.')
        return
    print('\n' + str(len(waiting)) + ' brokers insist on their own form. Of course they do.')
    print('Open this page. It has each form and your details ready to paste:\n  ' + write_forms_page(state))
    print('You press submit and you do the CAPTCHA. Then run "sent" with the references you finished.')
    print('If a form demands a photo of your ID, close the tab and run "noid" with its reference. The request carries on without it.')


def do_send(state, limit=20):
    password = None if os.environ.get('FDB_SMTP_PASS') or not smtp_host(need_profile(state)) else getpass.getpass('App password for ' + state['profile']['email'] + ' (not saved): ')
    done = send_all(state, limit, password)
    print(('Sent: ' + ', '.join(done) + '\nThe clock is running. They hate that.') if done else 'Nothing is waiting to be sent.')


def do_reply(state, ref=None, file=None):
    if ref and not file and not re.fullmatch(r'req-[0-9a-f]{8}', ref) and Path(ref).is_file():
        ref, file = None, ref  # they gave the file and left the reference to be found in it
    if file:
        text = Path(file).read_text(encoding='utf-8', errors='replace')
    else:
        print('Paste the reply. Finish with a line containing only a dot.')
        text = ''.join(iter(lambda: (lambda line: '' if line.strip() == '.' else line)(sys.stdin.readline()), ''))
    if not ref and not find_ref(state, text) and text.strip():
        ref = ask('Which request is this about (the req-... reference in the subject line)')
    result = read_reply(state, ref, text)
    print('\n' + result['broker'] + ': ' + result['meaning'])
    if result['answer']:
        answer = result['answer']
        print('\nYour answer is written. Reply to their email with this text. It is also on your send page:\n  ' + write_send_page(state) + '\n\n' + answer['body'])


def do_chase(state):
    out = chase(state)
    if not out['reminders'] and not out['complaints']:
        print('\nNobody to chase today. Come back when they are late. They will be late.')
    print(''.join('\nReminder for ' + item['broker'] + ' written.\n' for item in out['reminders']), end='')
    if out['reminders']:
        print('\nSend the reminders from your send page, or run "send":\n  ' + write_send_page(state))
    for item in out['complaints']:
        print('\n' + item['broker'] + ' ' + item['why'] + '.\nComplain to ' + item['regulator'] + '. ' + item['how'] + '\nInclude: ' + item['include'])


def do_inbox(state):
    found = inbox(state, None if not imap_host(need_profile(state)) or os.environ.get('FDB_SMTP_PASS') else getpass.getpass('App password for ' + state['profile']['email'] + ' (not saved): '))
    print('\n\n'.join(item['broker'] + ': ' + item['meaning'] for item in found) or 'No new replies.')
    if any(item['answer'] for item in found):
        print('\nYour answers are written. Send them from your send page, or run "send":\n  ' + write_send_page(state))


def do_more(state):
    print('\n' + ('\n\n'.join(more(state)) or 'Nothing checked for where you live yet. The letters are still yours to send.'))


MENU = [('setup', 'Tell it who you are', do_setup), ('brokers', 'Get the list of data brokers', do_import), ('letters', 'Write the deletion letters', do_letters),
        ('send', 'Send them from here (optional)', do_send), ('forms', 'Brokers with only a web form', do_forms), ('reply', 'A broker answered. What now?', do_reply), ('chase', 'Chase the quiet ones, complain about the rest', do_chase),
        ('status', 'Where things stand', lambda state: show(status(state))), ('more', 'More you can do for free', do_more), ('inbox', 'Read their replies from my mailbox', do_inbox)]


def explain(problem):
    return 'Your email provider refused that password. It wants an app password, not your normal one. ' + SMTP_HELP if isinstance(problem, smtplib.SMTPAuthenticationError) else 'That did not work: ' + str(problem)


def attempt(action, gap=''):
    try:
        return action() and 0 or 0  # whatever a command returns, working is 0
    except Oops as problem:
        print(gap + str(problem))
    except (EOFError, KeyboardInterrupt):
        print('\nStopped. Nothing was lost.')
        return 130
    except (OSError, smtplib.SMTPException) as problem:
        print(gap + explain(problem))
    return 1


def menu():
    print(BANNER)
    while True:
        print('\n' + ''.join('  ' + str(number) + '. ' + label + '\n' for number, (_, label, _) in enumerate(MENU, 1)) + '  0. Quit')
        try:
            choice = input('\nPick a number: ').strip()
        except (EOFError, KeyboardInterrupt):
            choice = '0'
            print()
        if choice in ('0', 'q', ''):
            print('Go outside. They cannot track that as well.')
            return
        if not choice.isdigit() or not 1 <= int(choice) <= len(MENU):
            continue
        state = load()
        attempt(lambda: MENU[int(choice) - 1][2](state), '\n')
        save(state)


# Every command: its name, what it does, and what it takes.
COMMANDS = [
    ('setup', 'tell it who you are', []), ('brokers', 'download the public list of data brokers', []),
    ('add', 'add one broker by hand', [('name', {}), ('email', {}), ('website', dict(nargs='?', default=''))]),
    ('letters', 'write deletion letters as drafts', [('--limit', dict(type=int, default=20)), ('--match', dict(default='', help='only brokers whose name contains this text'))]),
    ('find', 'look through the broker list by name', [('text', dict(nargs='?', default=''))]),
    ('skip', 'never write to brokers whose name contains this text', [('text', {}), ('--undo', dict(action='store_true', help='stop skipping them'))]),
    ('noid', 'a form demanded your ID: carry the request on without it', [('ref', {})]),
    ('send', 'send the drafts from your own mailbox', [('--limit', dict(type=int, default=20))]),
    ('forms', 'brokers with no email: their form and your details, ready to paste', [('--limit', dict(type=int, default=10))]),
    ('update-list', '(maintainers) write the bundled brokers.json', [('file', dict(nargs='?', default=str(BUNDLED_LIST))), ('--from', dict(dest='files', action='append', default=[], metavar='KEY=FILE', help='a registry file downloaded by hand, for example or=oregon.csv'))]),
    ('sent', 'record that you sent drafts yourself', [('refs', dict(nargs='+', help='references, or "all"'))]),
    ('reply', 'read a broker reply and get your answer', [('ref', dict(nargs='?', help='the req-... reference; leave out if the reply contains it')), ('file', dict(nargs='?', help='file with the reply text; otherwise paste it'))]),
    ('chase', 'reminders and complaint routes', []), ('share', 'print what you taught it, with nothing about you in it, to post as an issue', []),
    ('teach', 'it misread a reply: tell it what a few words meant, and it reads the next one itself', [('kind', {}), ('phrase', {})]),
    ('auto', 'the whole routine in one command; put it on a schedule', [('--limit', dict(type=int, default=20, help='how many new letters each run (default 20)')), ('--send', dict(action='store_true', help='also send, using the app password in FDB_SMTP_PASS')), ('--inbox', dict(action='store_true', help='also read replies from your mailbox, with the same app password'))]),
    ('inbox', 'read the brokers\' replies straight from your own mailbox; it only looks', []),
    ('status', 'where things stand', []), ('more', 'free official steps beyond brokers, for where you live', []), ('mcp', 'run as an MCP server for your AI', []),
]


def do_update_list(args):
    try:
        files = dict(item.split('=', 1) for item in args.files)
        if any(key not in [source['key'] for source in SOURCES] for key in files) or any('=' not in item for item in args.files):
            raise Oops('Use --from KEY=FILE with one of: ' + ', '.join(source['key'] for source in SOURCES))
        report = write_list(args.file, files)
    except (Oops, ValueError) as problem:
        print(str(problem))
        return 1
    print(''.join(label + name + '\n' for label, key in (('new:  ', 'new'), ('gone: ', 'gone'), ('problem: ', 'problems')) for name in report[key]) + report['line'])
    return 0


def do_find(state, text):
    found = find_brokers(state, text)
    print(''.join('  ' + broker['name'] + '  <' + (broker['email'] or 'no email, form only') + '>  ' + broker['status'] + '\n' for broker in found['brokers'])
          + str(found['matching']) + ' brokers match.' + (' Showing the first 50.' if found['matching'] > 50 else ''))


def do_skip(state, text, undo):
    names = skip_brokers(state, text, undo)['brokers']
    print(('No longer skipping ' if undo else 'Skipping ') + str(len(names)) + ' brokers' + (': ' + ', '.join(names[:8]) + (' ...' if len(names) > 8 else '') if names else '.'))


def do_noid(state, ref):
    result = form_demands_id(state, ref)
    print('\n' + result['broker'] + ': ' + result['meaning'])
    if result.get('answer'):
        print('\nYour answer is written and on your send page:\n  ' + write_send_page(state) + '\n\n' + result['answer']['body'])


def do_auto(state, limit, send, read):
    done = routine(state, limit, send, read)
    for label, value, page in [('Brokers new to the list: ', done['new_brokers'], None), ('Letters written:         ', done['letters_written'], None), ('Reminders written:       ', done['reminders_written'], None),
                               ('Sent:                    ', len(done['sent']), None), ('Waiting for you to send: ', done['waiting_to_send'], 'send_page'), ('Forms waiting for you:   ', done['forms_waiting'], 'forms_page')]:
        print(label + str(value) + ('   ' + done[page] if page and value else ''))
    if done['complaints_ready']:
        print('Brokers to complain about: ' + str(done['complaints_ready']) + '   run "chase" to see where')
    print('\n'.join(done['notes']), end='\n' if done['notes'] else '')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='fuck_data_brokers', description='Make data brokers delete you. Run with no command for the menu.')
    sub = parser.add_subparsers(dest='command')
    for name, text, takes in COMMANDS:
        command = sub.add_parser(name, help=text)
        for flag, options in takes:
            command.add_argument(flag, **options)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # a console that cannot print a broker's fancy apostrophe must not crash the tool
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass
    if args.command in (None, 'mcp', 'update-list'):
        return menu() if not args.command else mcp_serve() if args.command == 'mcp' else do_update_list(args)
    state = load()
    run = {
        'setup': lambda: do_setup(state), 'brokers': lambda: do_import(state), 'chase': lambda: do_chase(state), 'status': lambda: show(status(state)), 'more': lambda: do_more(state),
        'add': lambda: print('Added ' + add_broker(state, args.name, args.email, args.website)),
        'letters': lambda: do_letters(state, args.limit, args.match), 'find': lambda: do_find(state, args.text), 'skip': lambda: do_skip(state, args.text, args.undo),
        'noid': lambda: do_noid(state, args.ref), 'send': lambda: do_send(state, args.limit), 'forms': lambda: do_forms(state, args.limit),
        'sent': lambda: print('Marked as sent: ' + (', '.join(record_sent(state, args.refs)) or 'nothing')), 'reply': lambda: do_reply(state, args.ref, args.file), 'auto': lambda: do_auto(state, args.limit, args.send, args.inbox), 'inbox': lambda: do_inbox(state),
        'teach': lambda: print('Learned. Rules you taught it: ' + str(teach(state, args.kind, args.phrase)['rules_you_taught']) + '. Run "share" to give them to everyone.'),
        'share': lambda: print('\n'.join(share(state)) or 'Nothing taught yet.'),
    }
    return (attempt(run[args.command]), save(state))[0]


if __name__ == '__main__':
    sys.exit(main() or 0)

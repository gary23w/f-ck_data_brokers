#!/usr/bin/env python3
"""f*ck_data_brokers

Data brokers collect your name, address and habits without asking, sell them to
strangers, and then act confused when you object. This tool makes them delete you.

One file. No dependencies. Everything stays on your computer.

    python fuck_data_brokers.py          manual mode: a menu, no AI needed
    python fuck_data_brokers.py mcp      let your AI drive it over MCP
    python fuck_data_brokers.py --help   every command

What it does:
  1. Gets the list of registered data brokers from California's public registry.
  2. Writes each one a proper legal deletion and opt-out letter, in your name.
  3. When a broker insists on its web form, gives you the form and what to paste.
  4. Reads their replies and tells you what the excuse means and what to answer.
  5. Chases the ones that go quiet, and tells you where to complain.

What it never does: send your ID, send your street address, click their links,
get past a CAPTCHA for you, or believe a broker that says "we value your privacy".
"""
import argparse
import csv
import getpass
import html
import io
import json
import os
import re
import secrets
import smtplib
import sys
import time
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path

VERSION = '1.0.0'
DAY = 86400
REGISTRY_URL = 'https://cppa.ca.gov/data_broker_registry/registry.csv'
REGISTRY_CREDIT = 'Broker list: California Privacy Protection Agency data broker registry (cppa.ca.gov).'

# ---------------------------------------------------------------- the law

# Where you live decides which law your letter quotes.
PLACES = {
    'california': ('California', 'ccpa'),
    'us_other': ('Another US state', 'us_other'),
    'quebec': ('Quebec', 'quebec'),
    'canada_other': ('Canada, outside Quebec', 'pipeda'),
    'eu': ('European Union', 'gdpr'),
    'uk': ('United Kingdom', 'uk_gdpr'),
    'other': ('Anywhere else', 'policy'),
}

# Days a broker gets before you chase it.
DEADLINE_DAYS = {'ccpa': 45, 'us_other': 45, 'pipeda': 30, 'quebec': 30, 'gdpr': 30, 'uk_gdpr': 30, 'policy': 30}

LAW_NAME = {
    'ccpa': 'California Consumer Privacy Act deletion and opt-out request',
    'us_other': 'State privacy law deletion and opt-out request',
    'pipeda': 'PIPEDA withdrawal of consent and deletion request',
    'quebec': 'Quebec Law 25 cessation of dissemination request',
    'gdpr': 'GDPR Article 17 erasure and Article 21 objection',
    'uk_gdpr': 'UK GDPR Article 17 erasure and Article 21 objection',
    'policy': 'Deletion request under your privacy policy',
}

LAW_TEXT = {
    'ccpa': 'Under the California Consumer Privacy Act, Cal. Civ. Code section 1798.105, I request that you delete all personal information you hold about me and direct your service providers and contractors to do the same. Under section 1798.120 I also direct you not to sell or share my personal information. Please confirm within the time the law allows, and state the categories of information deleted or the specific reason any part of this request is declined.',
    'us_other': 'Under the privacy law of my state of residence and your own privacy policy, I request that you delete the personal information you hold about me and opt me out of the sale of personal information, targeted advertising and profiling. Please confirm the action taken or state the specific reason any part of this request is declined.',
    'pipeda': 'Under the Personal Information Protection and Electronic Documents Act (PIPEDA), I withdraw any consent to your collection, use and disclosure of my personal information (Schedule 1, Principle 4.3.8) and ask that it be destroyed, erased or made anonymous (Principle 4.5.3). Under Principle 4.9 I also ask you to confirm what personal information you hold about me and its source. Section 8 of PIPEDA requires a written response not later than thirty days after this request is received; a request that is not answered in that time is treated as refused.',
    'quebec': 'Under section 28.1 of Quebec\'s Act respecting the protection of personal information in the private sector, I request that you cease disseminating my personal information and de-index any hyperlink attached to my name that gives access to it. Under sections 28 and 30, I also request correction or deletion of the information and a written response within 30 days.',
    'gdpr': 'Under Article 17(1) of the General Data Protection Regulation, I request erasure of the personal data you hold about me, and under Article 21(1) and 21(2) I object to further processing, including for direct marketing. Article 12(3) requires a response without undue delay and in any event within one month; Article 17(2) requires you to inform other controllers to which you disclosed the data.',
    'uk_gdpr': 'Under Article 17(1) of the UK General Data Protection Regulation and the Data Protection Act 2018, I request erasure of the personal data you hold about me, and under Article 21 I object to further processing, including for direct marketing. A response is required within one month of receipt.',
    'policy': 'Under your privacy policy and any privacy law that applies where I live, I request that you delete the personal information you hold about me, stop publishing it, and confirm the action taken or the reason it is declined.',
}

# Where to complain when a broker refuses. Read from each regulator's own pages, November 2026.
REGULATOR = {
    'ccpa': ('the California Privacy Protection Agency', 'File at https://cppa.ca.gov/webapplications/complaint (anyone may complain; you do not need to live in California). The Attorney General also takes complaints: https://oag.ca.gov/contact/consumer-complaint-against-business-or-company'),
    'pipeda': ('the Office of the Privacy Commissioner of Canada', 'File at https://www.priv.gc.ca/en/report-a-concern/file-a-formal-privacy-complaint/file-a-complaint-about-a-business/ . They expect you to have tried the company first (you have). Complain within six months of the refusal.'),
    'quebec': ('the Commission d\'acces a l\'information du Quebec', 'Fill in the form at https://www.cai.gouv.qc.ca/uploads/pdfs/CAI_FO_Plainte_Citoyen.pdf and email it to cai.communications@cai.gouv.qc.ca .'),
    'uk_gdpr': ('the Information Commissioner\'s Office', 'File at https://ico.org.uk/make-a-complaint/data-protection-complaints/ . Complain within three months of the company\'s response and attach your original request.'),
    'gdpr': ('your national data protection authority', 'GDPR Article 77 gives you the right to complain to the data protection authority of the country where you live or work.'),
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


def read_registry(text):
    """Turn California's registry CSV into brokers. Nothing is invented: a row without a name is skipped."""
    rows = list(csv.reader(io.StringIO(text.lstrip('﻿'))))
    if not rows:
        raise Oops('The registry file is empty.')
    header = [' '.join(h.lower().split()) for h in rows[0]]

    def column(test):
        for index, title in enumerate(header):
            if test(title):
                return index
        raise Oops('The registry changed its columns. Nothing was imported.')

    col_id = column(lambda h: h.startswith('broker id'))
    col_name = column(lambda h: h.startswith('data broker name'))
    col_site = column(lambda h: h.startswith('data broker primary website') and 'exercise' not in h)
    col_mail = column(lambda h: 'primary contact email' in h)
    col_rights = column(lambda h: 'how consumers can exercise' in h)
    brokers = {}
    for row in rows[1:]:
        if len(row) <= max(col_id, col_name, col_site, col_mail, col_rights):
            continue
        number, name = row[col_id].strip(), ' '.join(row[col_name].split())
        if not number.isdigit() or not name:
            continue
        brokers['ca-' + number] = {'name': name[:200], 'email': clean_email(row[col_mail]), 'website': row[col_site].strip()[:300],
                                   'rights_url': row[col_rights].strip().split()[0][:300] if row[col_rights].strip() else '',
                                   'source': 'california registry'}
    return brokers


def import_registry(state, text=None):
    if text is None:
        request = urllib.request.Request(REGISTRY_URL, headers={'Accept': 'text/csv', 'User-Agent': 'fuck_data_brokers/' + VERSION})
        with urllib.request.urlopen(request, timeout=60) as response:
            text = response.read(8 * 1024 * 1024).decode('utf-8', 'replace')
    found = read_registry(text)
    if len(found) < 20:
        raise Oops('The registry returned almost nothing, so nothing was imported.')
    added = 0
    for key, broker in found.items():
        if key not in state['brokers']:
            added += 1
        state['brokers'][key] = {**state['brokers'].get(key, {}), **broker}
    return {'in_registry': len(found), 'new': added, 'with_email': sum(1 for b in found.values() if b['email'])}


BUNDLED_LIST = Path(__file__).resolve().parent / 'brokers.json'


def write_list(file):
    """Used by the scheduled GitHub Action: fetch the registry and save it as brokers.json next to this file."""
    state = {'brokers': {}}
    counts = import_registry(state)
    with open(file, 'w', encoding='utf-8', newline='\n') as out:  # the same bytes on every system, so the list only changes when the registry does
        out.write(json.dumps({'credit': REGISTRY_CREDIT, 'source': REGISTRY_URL, 'brokers': state['brokers']}, indent=1, ensure_ascii=False, sort_keys=True) + '\n')
    return counts


def load_brokers(state):
    """Get the broker list: live from the registry, or from the copy shipped with this tool when you are offline."""
    try:
        return {**import_registry(state), 'from': 'the live registry'}
    except Oops:
        raise
    except Exception:
        if not BUNDLED_LIST.exists():
            raise Oops('Could not reach the registry and there is no bundled list. Check your connection and try again.')
        found = json.loads(BUNDLED_LIST.read_text(encoding='utf-8'))['brokers']
        new = sum(1 for key in found if key not in state['brokers'])
        for key, broker in found.items():
            state['brokers'][key] = {**state['brokers'].get(key, {}), **broker}
        return {'in_registry': len(found), 'new': new, 'with_email': sum(1 for b in found.values() if b['email']), 'from': 'the list shipped with this tool'}


def add_broker(state, name, email, website=''):
    name, email = ' '.join(str(name).split()), clean_email(email)
    if not name or not email:
        raise Oops('A broker needs a name and a privacy email address.')
    key = 'me-' + secrets.token_hex(4)
    state['brokers'][key] = {'name': name, 'email': email, 'website': str(website).strip(), 'rights_url': '', 'source': 'added by you'}
    return key


# ---------------------------------------------------------------- the letters


def details(profile):
    lines = ['  Full legal name: ' + profile['name'], '  Email address: ' + profile['email']]
    where = ', '.join(part for part in (profile['city'], profile['region'], profile['country']) if part)
    if where:
        lines.append('  City and region (no street address): ' + where)
    return lines


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
        'I sent you the request below on ' + day(request['sent_at']) + ' (reference ' + request['ref'] + '). The time to respond has passed and I have no answer.',
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


def smtp_ready(profile):
    return bool(smtp_host(profile))


def gmail_link(to, subject, body):
    return 'https://mail.google.com/mail/?view=cm&fs=1&to=' + urllib.parse.quote(to) + '&su=' + urllib.parse.quote(subject) + '&body=' + urllib.parse.quote(body)


def write_send_page(state):
    """One page with every unsent letter: a button that opens it in your mail program, and copy buttons if that fails.
    Free, works with any mailbox, needs no password and no setup."""
    cards = []
    for request in state['requests'].values():
        if request['state'] != 'draft' or request.get('via') == 'form':
            continue
        broker = state['brokers'][request['broker']]
        to, subject, body = broker['email'], request['subject'], request['body']
        e = html.escape
        cards.append('<section><h2>' + e(broker['name']) + ' <small>' + e(request['ref']) + '</small></h2>'
                     '<p><a class="go" href="' + e(mailto(to, subject, body)) + '">Open in my mail app</a> '
                     '<a class="go" href="' + e(gmail_link(to, subject, body)) + '" target="_blank" rel="noopener">Open in Gmail</a></p>'
                     '<p>Or copy and paste: <button data-copy="' + e(to) + '">Copy address</button> <button data-copy="' + e(subject) + '">Copy subject</button> '
                     '<button data-copy="' + e(body) + '">Copy letter</button></p><pre>' + e(body) + '</pre></section>')
    page = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Letters to send</title>'
            '<style>body{font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem}section{border-top:2px solid #000;padding:1rem 0}'
            'pre{white-space:pre-wrap;background:#f4f4f4;padding:1rem}small{font-weight:400;color:#555}.go,button{display:inline-block;margin:.2rem .3rem .2rem 0;padding:.5rem .8rem;'
            'border:2px solid #000;background:#fff;color:#000;text-decoration:none;font:inherit;cursor:pointer}.go{background:#000;color:#fff}</style>'
            '<h1>' + str(len(cards)) + ' letters to send</h1><p>Each button opens a ready letter in your own email. Press send, then come back for the next one. '
            'When you are done, run <code>sent all</code> so the clock starts.</p>' + ''.join(cards) +
            '<script>document.addEventListener("click",function(e){var v=e.target.getAttribute&&e.target.getAttribute("data-copy");if(v===null)return;'
            'navigator.clipboard.writeText(v).then(function(){e.target.textContent="Copied"});});</script>')
    box = home() / 'outbox'
    box.mkdir(exist_ok=True)
    file = box / 'send.html'
    file.write_text(page, encoding='utf-8')
    return str(file)


def make_email(profile, to, subject, body):
    message = EmailMessage()
    message['From'], message['To'], message['Subject'] = profile['email'], to, subject
    message.set_content(body)
    return message


def write_draft(profile, to, subject, body, ref, label):
    box = home() / 'outbox'
    box.mkdir(exist_ok=True)
    file = box / (ref + '-' + label + '.eml')
    file.write_bytes(bytes(make_email(profile, to, subject, body)))
    return str(file)


def mailto(to, subject, body):
    return 'mailto:' + urllib.parse.quote(to) + '?subject=' + urllib.parse.quote(subject) + '&body=' + urllib.parse.quote(body)


def smtp_send(profile, messages, password=None):
    """Send through your own mailbox. Your password is read from the environment or asked for; it is never saved."""
    if not smtp_ready(profile):
        raise Oops(SMTP_HELP)
    host, port = smtp_host(profile), int(os.environ.get('FDB_SMTP_PORT') or 587)
    with smtplib.SMTP(host, port, timeout=60) as server:
        server.starttls()
        server.login(os.environ.get('FDB_SMTP_USER') or profile['email'], password or os.environ.get('FDB_SMTP_PASS') or '')
        for message in messages:
            server.send_message(message)
            time.sleep(2)  # no need to look like the spammers we are writing to


# ---------------------------------------------------------------- the work


def new_ref():
    return 'req-' + secrets.token_hex(4)


def log(request, text):
    request.setdefault('log', []).append(day(now()) + '  ' + text)


def prepare(state, limit=20):
    """Write first letters for brokers that have an email address and no request yet."""
    profile, law = need_profile(state), law_of(state)
    asked = {r['broker'] for r in state['requests'].values()}
    made = []
    for key, broker in sorted(state['brokers'].items(), key=lambda item: item[1]['name'].lower()):
        if len(made) >= limit:
            break
        if key in asked or not broker.get('email'):
            continue
        ref = new_ref()
        subject, body = first_letter(profile, broker, law, ref)
        request = {'ref': ref, 'broker': key, 'law': law, 'state': 'draft', 'subject': subject, 'body': body, 'sent_at': 0, 'due_at': 0, 'answers': []}
        request['draft'] = write_draft(profile, broker['email'], subject, body, ref, 'request')
        log(request, 'Letter written.')
        state['requests'][ref] = request
        made.append(request)
    return made


def form_url(broker):
    url = str(broker.get('rights_url') or '')
    return url if re.match(r'https?://[^\s"<>]+$', url) else ''


def form_text(profile, law, ref):
    return LAW_TEXT[law] + ' Please locate only records matching me; do not affect people who share my name. Reference ' + ref + '. ' + profile['name']


def form_waiting(request):
    return request['state'] == 'form' or request['state'] == 'draft' and request.get('via') == 'form'


def prepare_forms(state, limit=10):
    """For brokers with no email address: the form you fill in yourself, with everything ready to paste.
    You are the person, so you press submit and you do any CAPTCHA. Nothing is bypassed and nothing is submitted for you."""
    profile, law = need_profile(state), law_of(state)
    asked = {r['broker'] for r in state['requests'].values()}
    made = []
    for key, broker in sorted(state['brokers'].items(), key=lambda item: item[1]['name'].lower()):
        if len(made) >= limit:
            break
        if key in asked or broker.get('email') or not form_url(broker):
            continue
        ref = new_ref()
        request = {'ref': ref, 'broker': key, 'law': law, 'state': 'draft', 'via': 'form', 'subject': LAW_NAME[law] + ' [' + ref + ']', 'body': form_text(profile, law, ref),
                   'sent_at': 0, 'due_at': 0, 'answers': []}
        log(request, 'Form sheet written.')
        state['requests'][ref] = request
        made.append(request)
    return made


def write_forms_page(state):
    profile, cards, e = need_profile(state), [], html.escape
    where = ', '.join(part for part in (profile['city'], profile['region'], profile['country']) if part)
    for request in state['requests'].values():
        if not form_waiting(request):
            continue
        broker = state['brokers'][request['broker']]
        cards.append('<section><h2>' + e(broker['name']) + ' <small>' + e(request['ref']) + '</small></h2>'
                     '<p><a class="go" href="' + e(form_url(broker)) + '" target="_blank" rel="noopener noreferrer">Open their form</a></p>'
                     '<p>Paste as needed: <button data-copy="' + e(profile['name']) + '">Copy name</button> <button data-copy="' + e(profile['email']) + '">Copy email</button> '
                     + ('<button data-copy="' + e(where) + '">Copy city</button> ' if where else '') + '<button data-copy="' + e(request['body']) + '">Copy request text</button></p>'
                     '<pre>' + e(request['body']) + '</pre></section>')
    page = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Forms to fill in</title>'
            '<style>body{font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem}section{border-top:2px solid #000;padding:1rem 0}'
            'pre{white-space:pre-wrap;background:#f4f4f4;padding:1rem}small{font-weight:400;color:#555}.go,button{display:inline-block;margin:.2rem .3rem .2rem 0;padding:.5rem .8rem;'
            'border:2px solid #000;background:#fff;color:#000;text-decoration:none;font:inherit;cursor:pointer}.go{background:#000;color:#fff}</style>'
            '<h1>' + str(len(cards)) + ' brokers that hide behind a form</h1><p>These ones will not act on an email, so you fill in their form. Open it, pick "delete" and "do not sell or share", '
            'paste your details, do their CAPTCHA, submit. Give them only what is on this page. If a form demands a photo of your ID, close the tab. '
            'When you are done, run <code>sent</code> with the references you submitted.</p>' + ''.join(cards) +
            '<script>document.addEventListener("click",function(e){var v=e.target.getAttribute&&e.target.getAttribute("data-copy");if(v===null)return;'
            'navigator.clipboard.writeText(v).then(function(){e.target.textContent="Copied"});});</script>')
    box = home() / 'outbox'
    box.mkdir(exist_ok=True)
    file = box / 'forms.html'
    file.write_text(page, encoding='utf-8')
    return str(file)


def mark_sent(state, refs):
    done = []
    for ref in refs:
        request = state['requests'].get(ref)
        if request and request['state'] in ('draft', 'form'):
            words = 'Form submitted' if form_waiting(request) else 'Sent'
            request.update(state='sent', sent_at=request['sent_at'] or now(), due_at=now() + DEADLINE_DAYS[request['law']] * DAY)
            log(request, words + '. They have until ' + day(request['due_at']) + '.')
            done.append(ref)
    return done


def send_drafts(state, limit=20, password=None):
    profile = need_profile(state)
    drafts = [r for r in state['requests'].values() if r['state'] == 'draft' and r.get('via') != 'form'][:limit]
    smtp_send(profile, [make_email(profile, state['brokers'][r['broker']]['email'], r['subject'], r['body']) for r in drafts], password)
    return mark_sent(state, [r['ref'] for r in drafts])


# What a broker's reply usually means. Checked in this order; the first match wins.
REPLY_RULES = [
    ('done', r'\b(?:has|have) been (?:deleted|removed|erased|suppressed|opted out|processed|completed)\b|\b(?:we have|we\'ve) (?:deleted|removed|erased|suppressed|completed|processed|opted you out)\b|\bsuccessfully (?:deleted|removed|opted|processed)\b|\brequest (?:is|has been) complete'),
    ('no_record', r'\b(?:found no|did not (?:locate|find)|(?:were|are) unable to (?:locate|find)|could not (?:find|locate)|do not (?:have|hold)|no records? (?:matching|found|were found))\b[^.!?]{0,120}\b(?:records?|information|data|match)'),
    ('wants_id', r'\b(?:government[- ]issued|photo id|driver\'?s licen[cs]e|passport|identity document|proof of identity|copy of (?:your )?id|notari[sz]ed|power of attorney)\b'),
    ('use_form', r'\b(?:web ?form|online form|our form|privacy (?:portal|center|request page)|opt[- ]?out (?:form|page|portal)|submit (?:your|the|a) request (?:through|via|using|at|on)|complete (?:the|our) (?:form|portal)|does not (?:accept|process)[^.!?]{0,60}(?:by|via) e-?mail|not monitored)\b'),
    ('wants_details', r'\b(?:additional|more|further) (?:information|details|identifiers)\b|\binsufficient information\b|\bplease (?:provide|reply with|send)\b[^.!?]{0,80}\b(?:date of birth|address|phone|full name)\b'),
    ('refused', r'\b(?:unable to (?:process|verify|honou?r|complete|fulfil)|cannot (?:process|verify|honou?r|complete|fulfil)|can\'?t (?:process|verify)|(?:is|has been|are) (?:denied|declined|rejected)|we (?:deny|decline)|not (?:subject to|covered by|applicable)|does not apply|exempt)\b'),
    ('received', r'\b(?:received|receipt of|acknowledge|thank you for (?:contacting|your)|ticket|case (?:number|id)|will (?:respond|review|process|get back))\b'),
]

MEANING = {
    'done': 'They say it is done. A broker saying so is a claim, not proof, but it is the answer you wanted.',
    'no_record': 'They say they hold nothing on you. Fine. One down.',
    'wants_id': 'They want your ID. A company that sells personal data wants more personal data. No.',
    'use_form': 'They want you to use their web form. You already asked in writing; they just prefer a form with a CAPTCHA.',
    'wants_details': 'They want more details about you before they will delete the details they have about you.',
    'refused': 'They refused. Answer once; if they refuse again, send the final notice.',
    'received': 'An automatic "we got it". Means nothing yet. The clock keeps running.',
    'unclear': 'I cannot tell what this means. Read it yourself; if they ask for a link click or an ID, do not.',
}


def classify(text):
    flat = ' '.join(str(text).lower().split())
    for kind, pattern in REPLY_RULES:
        if re.search(pattern, flat):
            return kind
    return 'unclear'


def read_reply(state, ref, text):
    """Record a broker's reply and say what to do. Returns the answer to send, if there is one."""
    profile = need_profile(state)
    request = state['requests'].get(ref)
    if not request:
        raise Oops('No request with reference ' + ref + '.')
    if request['state'] == 'draft':
        raise Oops('That letter has not been sent yet.')
    broker, kind = state['brokers'][request['broker']], classify(text)
    result = {'ref': ref, 'broker': broker['name'], 'kind': kind, 'meaning': MEANING[kind], 'answer': None}
    log(request, 'Reply: ' + kind + '.')
    if kind == 'use_form' and form_url(broker):
        # You are right here, so filling their form beats arguing about it by email.
        request.update(state='form', via='form', body=form_text(profile, request['law'], ref))
        result['form'] = form_url(broker)
        result['meaning'] += ' It is on your forms page now: run "forms".'
    elif kind == 'done':
        request['state'] = 'done'
    elif kind == 'no_record':
        request['state'] = 'no_record'
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
                result['answer'] = {'to': broker['email'], 'subject': subject, 'body': body, 'draft': write_draft(profile, broker['email'], subject, body, ref, 'final-notice'), 'kind': 'final_notice'}
                log(request, 'Final notice written. They have ' + str(NOTICE_DAYS) + ' days.')
            else:
                result['meaning'] += ' No law backs this request, so it ends here.'
        else:
            subject, body = answer_letter(profile, broker, request['law'], request, kind)
            request['answers'].append(kind)
            request['state'] = 'answered'
            result['answer'] = {'to': broker['email'], 'subject': subject, 'body': body, 'draft': write_draft(profile, broker['email'], subject, body, ref, 'answer-' + kind), 'kind': kind}
            log(request, 'Answer written (' + kind + ').')
    return result


def chase(state):
    """Reminders for brokers that went quiet, and complaint routes for the ones that refused twice."""
    profile, out = need_profile(state), {'reminders': [], 'complaints': []}
    for request in state['requests'].values():
        broker = state['brokers'][request['broker']]
        if request['state'] in ('sent', 'acknowledged', 'answered') and request['due_at'] and request['due_at'] < now() and not request.get('reminded'):
            subject, body = reminder_letter(profile, broker, request['law'], request)
            request['reminded'] = now()
            log(request, 'Reminder written.')
            out['reminders'].append({'ref': request['ref'], 'broker': broker['name'], 'to': broker['email'], 'subject': subject, 'body': body,
                                     'draft': write_draft(profile, broker['email'], subject, body, request['ref'], 'reminder')})
        if 'final_notice' in request['answers'] and request.get('notice_due', 0) < now() and request['state'] not in ('done', 'no_record'):
            regulator, how = REGULATOR[request['law']]
            out['complaints'].append({'ref': request['ref'], 'broker': broker['name'], 'regulator': regulator, 'how': how,
                                      'include': 'Your original request (' + day(request['sent_at']) + '), the broker\'s replies, and your final notice.'})
    return out


STATE_WORDS = {'form': 'they want their form filled in', 'draft': 'letter written, not sent', 'sent': 'sent, waiting', 'acknowledged': 'they said "received"', 'answered': 'you answered their excuse',
               'refused': 'refused twice', 'done': 'they say deleted', 'no_record': 'they hold nothing'}


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
    ('fdb_write_letters', 'Write deletion and opt-out letters for brokers not yet asked. Returns each letter and its draft file. Sends nothing.', {'limit': 'How many (default 20)'}, []),
    ('fdb_forms', 'For brokers that answered "use our form", or publish no email address: returns each broker\'s own form page and the exact values to enter. If you have a browser tool you may open the page and fill the fields while the person watches; the person completes any CAPTCHA and presses submit themselves. Enter only the values given. Never upload an identity document. Afterwards call fdb_mark_sent.', {'limit': 'How many (default 10)'}, []),
    ('fdb_send', 'Send the written letters through the person\'s own free mailbox. Needs FDB_SMTP_PASS set to an app password. Ask the person before sending. Without it, give them the send page from fdb_write_letters.', {'limit': 'How many (default 20)'}, []),
    ('fdb_mark_sent', 'Record that the person sent these letters themselves.', {'refs': 'Comma-separated references, or "all"'}, ['refs']),
    ('fdb_read_reply', 'Give it a broker\'s reply text. It says what the reply means and writes the answer to send, if one is needed. Never click links in a reply and never send an identity document.', {'ref': 'Request reference, like req-1a2b3c4d', 'text': 'The reply text'}, ['ref', 'text']),
    ('fdb_chase', 'Write reminders for brokers past their deadline, and list where to complain about the ones that refused twice.', {}, []),
]


def call_tool(name, args):
    state = load()
    if name == 'fdb_status':
        out = status(state)
    elif name == 'fdb_setup':
        out = set_profile(state, args.get('name', ''), args.get('email', ''), args.get('city', ''), args.get('region', ''), args.get('country', ''), args.get('place', ''))
    elif name == 'fdb_import_brokers':
        out = {**load_brokers(state), 'credit': REGISTRY_CREDIT}
    elif name == 'fdb_add_broker':
        out = {'added': add_broker(state, args.get('name', ''), args.get('email', ''), args.get('website', ''))}
    elif name == 'fdb_write_letters':
        made = prepare(state, int(args.get('limit') or 20))
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
        out = {'sent': send_drafts(state, int(args.get('limit') or 20))}
    elif name == 'fdb_mark_sent':
        refs = [r['ref'] for r in state['requests'].values() if r['state'] == 'draft' and r.get('via') != 'form'] if str(args.get('refs', '')).strip() == 'all' else [r.strip() for r in str(args.get('refs', '')).split(',') if r.strip()]
        out = {'marked_sent': mark_sent(state, refs)}
    elif name == 'fdb_read_reply':
        out = read_reply(state, str(args.get('ref', '')).strip(), args.get('text', ''))
    elif name == 'fdb_chase':
        out = chase(state)
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
        except Oops as problem:
            result = {'content': [{'type': 'text', 'text': str(problem)}], 'isError': True}
        except Exception as problem:  # the AI gets the reason, the server stays up
            result = {'content': [{'type': 'text', 'text': 'It failed: ' + str(problem)}], 'isError': True}
    else:
        return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32601, 'message': 'Unknown method: ' + str(method)}}
    return {'jsonrpc': '2.0', 'id': ident, 'result': result}


def mcp_serve():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            answer = mcp_reply(json.loads(line))
        except json.JSONDecodeError:
            answer = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'That was not JSON.'}}
        if answer is not None:
            sys.stdout.write(json.dumps(answer) + '\n')
            sys.stdout.flush()


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


def do_setup(state):
    old = state.get('profile') or {}
    print('\nWho are we deleting? This stays on your computer.')
    name = ask('Your full legal name', old.get('name', ''))
    email = ask('Your email address (brokers reply here)', old.get('email', ''))
    city, region, country = ask('City', old.get('city', '')), ask('State or province', old.get('region', '')), ask('Country', old.get('country', ''))
    print('Where do you live? It decides which law your letters quote.')
    for key, (label, _) in PLACES.items():
        print('   ' + key.ljust(13) + label)
    set_profile(state, name, email, city, region, country, ask('Pick one', old.get('place', 'other')))
    print('Saved. No street address, no birthday, no ID. They get the minimum.')


def do_import(state):
    print('\nFetching the list of companies that registered, in writing, as sellers of people...')
    print(json.dumps(load_brokers(state), indent=1))
    print(REGISTRY_CREDIT)


def do_letters(state, limit=20):
    made = prepare(state, limit)
    if not made:
        print('\nNo new letters to write. Either everyone has been asked, or you have no brokers yet (run "brokers").')
        return
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


def do_send(state, limit=20):
    password = None if os.environ.get('FDB_SMTP_PASS') or not smtp_ready(need_profile(state)) else getpass.getpass('App password for ' + state['profile']['email'] + ' (not saved): ')
    print('Sent: ' + ', '.join(send_drafts(state, limit, password)) + '\nThe clock is running. They hate that.')


def do_reply(state, ref=None, file=None):
    ref = ref or ask('Which request is the reply about (the req-... reference in the subject)')
    if file:
        text = Path(file).read_text(encoding='utf-8', errors='replace')
    else:
        print('Paste the reply. Finish with a line containing only a dot.')
        lines = []
        while True:
            line = sys.stdin.readline()
            if not line or line.strip() == '.':
                break
            lines.append(line)
        text = ''.join(lines)
    result = read_reply(state, ref, text)
    print('\n' + result['broker'] + ': ' + result['meaning'])
    if result['answer']:
        answer = result['answer']
        print('\nYour answer is written. Reply to their email with this text, or open the draft:\n  ' + answer['draft'] + '\n\n' + answer['body'])


def do_chase(state):
    out = chase(state)
    if not out['reminders'] and not out['complaints']:
        print('\nNobody to chase today. Come back when they are late. They will be late.')
    for item in out['reminders']:
        print('\nReminder for ' + item['broker'] + ' written: ' + item['draft'])
    for item in out['complaints']:
        print('\n' + item['broker'] + ' refused twice and ignored the final notice.')
        print('Complain to ' + (item['regulator'] or 'nobody, sadly') + '. ' + item['how'])
        print('Include: ' + item['include'])


MENU = [('setup', 'Tell it who you are', do_setup), ('brokers', 'Get the list of data brokers', do_import), ('letters', 'Write the deletion letters', do_letters),
        ('send', 'Send them from here (optional)', do_send), ('forms', 'Brokers with only a web form', do_forms), ('reply', 'A broker answered. What now?', do_reply), ('chase', 'Chase the quiet ones, complain about the rest', do_chase),
        ('status', 'Where things stand', lambda state: show(status(state)))]


def menu():
    print(BANNER)
    while True:
        print()
        for number, (_, label, _) in enumerate(MENU, 1):
            print('  ' + str(number) + '. ' + label)
        print('  0. Quit')
        choice = input('\nPick a number: ').strip()
        if choice in ('0', 'q', ''):
            print('Go outside. They cannot track that as well.')
            return
        if not choice.isdigit() or not 1 <= int(choice) <= len(MENU):
            continue
        state = load()
        try:
            MENU[int(choice) - 1][2](state)
        except Oops as problem:
            print('\n' + str(problem))
        except (OSError, smtplib.SMTPException) as problem:
            print('\nThat did not work: ' + str(problem))
        save(state)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='fuck_data_brokers', description='Make data brokers delete you. Run with no command for the menu.')
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('setup', help='tell it who you are')
    sub.add_parser('brokers', help='download the public list of data brokers')
    add = sub.add_parser('add', help='add one broker by hand')
    add.add_argument('name'), add.add_argument('email'), add.add_argument('website', nargs='?', default='')
    letters = sub.add_parser('letters', help='write deletion letters as drafts')
    letters.add_argument('--limit', type=int, default=20)
    send = sub.add_parser('send', help='send the drafts from your own mailbox')
    send.add_argument('--limit', type=int, default=20)
    forms = sub.add_parser('forms', help='brokers with no email: their form and your details, ready to paste')
    forms.add_argument('--limit', type=int, default=10)
    update = sub.add_parser('update-list', help='(maintainers) write the bundled brokers.json')
    update.add_argument('file', nargs='?', default=str(BUNDLED_LIST))
    sent = sub.add_parser('sent', help='record that you sent drafts yourself')
    sent.add_argument('refs', nargs='+', help='references, or "all"')
    reply = sub.add_parser('reply', help='read a broker reply and get your answer')
    reply.add_argument('ref'), reply.add_argument('file', nargs='?', help='file with the reply text; otherwise paste it')
    sub.add_parser('chase', help='reminders and complaint routes')
    sub.add_parser('status', help='where things stand')
    sub.add_parser('mcp', help='run as an MCP server for your AI')
    args = parser.parse_args(argv)
    if args.command == 'mcp':
        return mcp_serve()
    if not args.command:
        return menu()
    if args.command == 'update-list':
        print(json.dumps(write_list(args.file)))
        return 0
    state = load()
    try:
        if args.command == 'setup':
            do_setup(state)
        elif args.command == 'brokers':
            do_import(state)
        elif args.command == 'add':
            print('Added ' + add_broker(state, args.name, args.email, args.website))
        elif args.command == 'letters':
            do_letters(state, args.limit)
        elif args.command == 'send':
            do_send(state, args.limit)
        elif args.command == 'forms':
            do_forms(state, args.limit)
        elif args.command == 'sent':
            refs = [r['ref'] for r in state['requests'].values() if r['state'] == 'draft' and r.get('via') != 'form'] if args.refs == ['all'] else args.refs
            print('Marked as sent: ' + (', '.join(mark_sent(state, refs)) or 'nothing'))
        elif args.command == 'reply':
            do_reply(state, args.ref, args.file)
        elif args.command == 'chase':
            do_chase(state)
        elif args.command == 'status':
            show(status(state))
    except Oops as problem:
        print(str(problem))
        save(state)
        return 1
    save(state)
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)

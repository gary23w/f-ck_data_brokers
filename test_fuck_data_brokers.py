"""Tests. Run with:  python -m unittest -v

Everything runs in a temporary folder with made-up people and brokers. Nothing is sent and nothing is downloaded.
"""
import json
import os
import ssl
import tempfile
import contextlib
import io
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

import fuck_data_brokers as fdb

REGISTRY = (
    '﻿Broker ID,Data broker name:,"Doing Business As (DBA)\n if applicable:",Data broker primary website:,'
    'Data broker primary contact email address:,Data broker country:,'
    "Data broker's primary website that contains details on how consumers can exercise their CA Consumer Privacy rights:\n"
    + ''.join(str(n) + ',Example Broker ' + str(n) + ',,https://broker' + str(n) + '.example.com,Privacy@Broker' + str(n) + '.example.com,US,https://broker'
              + str(n) + '.example.com/privacy\n' for n in range(1, 31))
    + 'x,Not A Row,,,,,\n'
)

OREGON = (
    'LICENSE NO.,FACILITY NAME,DOING BUSINESS AS,UPDATE DATE,OPT OUT EMAIL,OPT OUT STREET ADDRESS,OPT OUT PHONE,OPT OUT WEBSITE,ADDITIONAL WEBSITE,OPT OUT OTHER METHODS,OPT OUT NARRATIVE\n'
    '"DATA-00009","Example Broker 7 LLC","Seven","01/03/2025","OptOut@Broker7.example.com","1 Sample Street","555-0100","broker7.example.com","","https://broker7.example.com/dont_sell","Visit our page"\n'
    '"DATA-00010","Brand New Broker LLC","","11/07/2025","privacy@brandnew.example.com","","","brandnew.example.com","","call us","Email us"\n'
    '"DATA-00011","Form Only Broker Inc","","12/16/2025","","","","formonly.example.com","","",""\n'
    '"","No Licence Row","","","","","","","","",""\n'
)
TEXAS = (
    'Registration Number,Full Legal Name,Data Broker Email Address,Website,Consumer Rights Link\n'
    '20240001,"Brand New Broker, LLC",tx@brandnew.example.com,brandnew.example.com,https://brandnew.example.com/rights\n'
    '20240002,Texas Only Broker LLC,Privacy@TexasOnly.example.com,www.texasonly.example.com,https://texasonly.example.com/optout\n'
    'n/a,No Number Row,,,\n'
)



class Case(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        os.environ['FDB_HOME'] = self.folder.name
        for key in ('FDB_SMTP_HOST', 'FDB_SMTP_PASS', 'FDB_SMTP_USER'):
            os.environ.pop(key, None)
        self.state = fdb.load()

    def tearDown(self):
        self.folder.cleanup()

    def person(self, place='california'):
        fdb.set_profile(self.state, 'Avery Exampleton', 'avery@example.com', 'Sampleville', 'CA', 'USA', place)
        fdb.import_registry(self.state, REGISTRY)

    def sent_request(self, place='california'):
        self.person(place)
        request = fdb.prepare(self.state, 1)[0]
        fdb.mark_sent(self.state, [request['ref']])
        return request

    def test_profile_rejects_nonsense_and_keeps_only_the_minimum(self):
        for name, email, place in [('Avery', 'avery@example.com', 'california'), ('Avery Exampleton', 'nope', 'california'), ('Avery Exampleton', 'avery@example.com', 'mars')]:
            with self.assertRaises(fdb.Oops):
                fdb.set_profile(self.state, name, email, '', '', '', place)
        profile = fdb.set_profile(self.state, '  Avery   Exampleton ', 'avery@example.com', 'Sampleville', 'CA', 'USA', 'california')
        self.assertEqual(profile['name'], 'Avery Exampleton')
        self.assertEqual(sorted(profile), ['city', 'country', 'email', 'name', 'place', 'region'], 'no street, birthday or ID is ever stored')

    def test_registry_import_reads_real_columns_and_invents_nothing(self):
        counts = fdb.import_registry(self.state, REGISTRY)
        self.assertEqual(counts, {'in_registry': 30, 'new': 30, 'with_email': 30})
        broker = self.state['brokers']['ca-7']
        self.assertEqual(broker['name'], 'Example Broker 7')
        self.assertEqual(broker['email'], 'privacy@broker7.example.com')
        self.assertEqual(broker['rights_url'], 'https://broker7.example.com/privacy')
        self.assertEqual(fdb.import_registry(self.state, REGISTRY)['new'], 0, 'importing twice adds nothing')
        with self.assertRaises(fdb.Oops):
            fdb.import_registry(self.state, 'Wrong,Columns\n1,2\n')
        with self.assertRaises(fdb.Oops):
            fdb.import_registry(self.state, REGISTRY.split('\n')[0] + '\n' + '\n'.join(REGISTRY.split('\n')[2:5]) + '\n')

    def test_each_place_gets_its_own_law_and_the_letter_carries_no_street_address(self):
        cites = {'california': '1798.105', 'us_other': 'my state of residence', 'quebec': 'section 28.1', 'canada_other': 'Principle 4.3.8', 'eu': 'Article 17(1) of the General Data Protection Regulation',
                 'uk': 'UK General Data Protection Regulation', 'other': 'your privacy policy'}
        for place, cite in cites.items():
            state = {'profile': None, 'brokers': {}, 'requests': {}}
            fdb.set_profile(state, 'Avery Exampleton', 'avery@example.com', 'Sampleville', 'CA', 'USA', place)
            fdb.add_broker(state, 'Example Broker', 'privacy@broker.example.com')
            request = fdb.prepare(state)[0]
            self.assertIn(cite, request['body'], place)
            self.assertIn('This is my own request', request['body'])
            self.assertIn('Full legal name: Avery Exampleton', request['body'])
            self.assertIn('City and region (no street address): Sampleville, CA, USA', request['body'])
            self.assertIn(request['ref'], request['subject'])
            self.assertTrue(Path(request['draft']).exists())
        self.assertEqual(set(fdb.PLACES[p][1] for p in fdb.PLACES), set(fdb.LAW_TEXT), 'every place has a letter')
        self.assertEqual(set(fdb.LAW_TEXT), set(fdb.DEADLINE_DAYS) & set(fdb.REGULATOR) & set(fdb.LAW_NAME))

    def test_letters_are_written_once_per_broker_and_respect_the_limit(self):
        self.person()
        self.assertEqual(len(fdb.prepare(self.state, 5)), 5)
        self.assertEqual(len(fdb.prepare(self.state, 100)), 25)
        self.assertEqual(fdb.prepare(self.state, 100), [], 'nobody is written to twice')
        refs = list(self.state['requests'])
        self.assertEqual(len(fdb.mark_sent(self.state, refs)), 30)
        self.assertEqual(fdb.mark_sent(self.state, refs), [], 'sent once')
        request = self.state['requests'][refs[0]]
        self.assertEqual(request['due_at'] - request['sent_at'], 45 * fdb.DAY)

    def test_replies_are_read_for_what_they_mean(self):
        cases = {
            'Your information has been deleted from our systems.': 'done',
            'We have removed your records and opted you out of future sales.': 'done',
            'We were unable to locate any records matching the information provided.': 'no_record',
            'To proceed, please send a copy of your government-issued photo ID.': 'wants_id',
            'Please submit your request through our privacy portal.': 'use_form',
            'This mailbox does not accept privacy requests by email.': 'use_form',
            'We need additional information to locate your record.': 'wants_details',
            'We are unable to process your request.': 'refused',
            'Your request has been denied.': 'refused',
            'Thank you for contacting us. We have received your request. Ticket 48213.': 'received',
            'Greetings from the support team.': 'unclear',
            # Replies that were once misread. A wrong "no record" would close a request that is still open.
            'We do not have sufficient information to verify your identity. Please provide additional information.': 'wants_details',
            'Your data will be deleted within 45 days.': 'received',
            'Your request cannot be completed at this time.': 'refused',
            'We have not deleted your data because we could not verify you.': 'refused',
            'Unfortunately your information cannot be removed.': 'refused',
            'We have no record of you in our systems.': 'no_record',
            'We do not hold any personal information about you.': 'no_record',
            'To opt out, please visit https://example.com/optout': 'use_form',
            'Please click the link below to verify your email address and confirm your request.': 'click_link',
            'Your information has been deleted. If you need additional information, contact us.': 'done',
        }
        for text, kind in cases.items():
            self.assertEqual(fdb.classify(text), kind, text)
        self.assertEqual(set(fdb.MEANING), {kind for kind, _ in fdb.REPLY_RULES} | {'unclear'})

    def test_an_id_demand_is_answered_once_then_gets_a_final_notice_then_the_regulator(self):
        request = self.sent_request('california')
        ref = request['ref']
        first = fdb.read_reply(self.state, ref, 'Please send a copy of your driver\'s license.')
        self.assertEqual(first['kind'], 'wants_id')
        self.assertIn('I do not agree to send one', first['answer']['body'])
        self.assertIn('11 CCR section 7026', first['answer']['body'])
        self.assertEqual(first['answer']['to'], 'privacy@broker1.example.com')
        second = fdb.read_reply(self.state, ref, 'We still require your photo ID.')
        self.assertEqual(second['answer']['kind'], 'final_notice')
        self.assertIn('I will file a complaint about this refusal with the California Privacy Protection Agency', second['answer']['body'])
        third = fdb.read_reply(self.state, ref, 'We still require your photo ID.')
        self.assertIsNone(third['answer'], 'the notice is never repeated')
        self.assertEqual(fdb.chase(self.state)['complaints'], [], 'not before the notice period ends')
        self.state['requests'][ref]['notice_due'] = fdb.now() - 1
        complaint = fdb.chase(self.state)['complaints'][0]
        self.assertEqual(complaint['regulator'], 'the California Privacy Protection Agency')
        self.assertIn('https://cppa.ca.gov/webapplications/complaint', complaint['how'])

    def test_a_request_with_no_law_behind_it_never_threatens_a_complaint(self):
        request = self.sent_request('other')
        fdb.read_reply(self.state, request['ref'], 'Your request has been denied.')
        again = fdb.read_reply(self.state, request['ref'], 'Your request has been denied.')
        self.assertIsNone(again['answer'])
        self.assertIn('No law backs this request', again['meaning'])

    def test_a_broker_that_insists_on_its_form_goes_to_the_forms_page_for_the_person_to_fill(self):
        request = self.sent_request()
        result = fdb.read_reply(self.state, request['ref'], 'Please submit your request through our privacy portal.')
        self.assertEqual(result['form'], 'https://broker1.example.com/privacy')
        self.assertIsNone(result['answer'])
        self.assertEqual(self.state['requests'][request['ref']]['state'], 'form')
        page = Path(fdb.write_forms_page(self.state)).read_text(encoding='utf-8')
        self.assertIn('href="https://broker1.example.com/privacy"', page)
        self.assertIn('data-copy="Avery Exampleton"', page)
        self.assertIn('do their CAPTCHA', page)
        self.assertEqual(fdb.mark_sent(self.state, [request['ref']]), [request['ref']])
        self.assertEqual(self.state['requests'][request['ref']]['state'], 'sent')
        # A form address that is not a plain web address is never offered.
        self.assertEqual(fdb.form_url({'rights_url': 'javascript:alert(1)'}), '')
        self.assertEqual(fdb.form_url({'rights_url': 'https://x.example.com/a b'}), '')
        # Brokers often register the address without https:// in front; that is still their page.
        self.assertEqual(fdb.form_url({'rights_url': 'www.example.com'}), 'https://www.example.com')
        self.assertEqual(fdb.form_url({'rights_url': 'example.com/privacy;'}), 'https://example.com/privacy')
        self.assertEqual(fdb.form_url({'rights_url': 'call us', 'website': 'https://example.com'}), 'https://example.com')
        self.assertEqual(fdb.form_url({'rights_url': 'call us on the phone', 'website': ''}), '')

    def test_done_and_no_record_close_the_request_and_quiet_brokers_get_one_reminder(self):
        self.person()
        a, b, c = fdb.prepare(self.state, 3)
        fdb.mark_sent(self.state, [a['ref'], b['ref'], c['ref']])
        fdb.read_reply(self.state, a['ref'], 'Your information has been deleted.')
        fdb.read_reply(self.state, b['ref'], 'We could not find any records matching your details.')
        self.assertEqual((a['state'], b['state']), ('done', 'no_record'))
        self.assertEqual(fdb.chase(self.state)['reminders'], [])
        c['due_at'] = fdb.now() - 1
        reminders = fdb.chase(self.state)['reminders']
        self.assertEqual([r['ref'] for r in reminders], [c['ref']])
        self.assertIn('The time to respond has passed', reminders[0]['body'])
        self.assertEqual(fdb.chase(self.state)['reminders'], [], 'one reminder, not a campaign')

    def test_the_send_page_needs_no_password_and_escapes_what_brokers_put_in_their_names(self):
        fdb.set_profile(self.state, 'Avery Exampleton', 'avery@example.com', '', '', '', 'uk')
        fdb.add_broker(self.state, 'Bad <script>alert(1)</script> Broker', 'privacy@broker.example.com')
        fdb.prepare(self.state)
        page = Path(fdb.write_send_page(self.state)).read_text(encoding='utf-8')
        self.assertNotIn('<script>alert(1)', page)
        self.assertIn('href="mailto:privacy%40broker.example.com?subject=', page)
        self.assertIn('https://mail.google.com/mail/?view=cm', page)
        self.assertIn('Copy letter', page)
        self.assertIn('background:#fff;color:#000', page, 'readable in a dark-themed browser too')

    def test_free_mailboxes_are_recognised_and_sending_without_a_known_server_explains_itself(self):
        self.assertEqual(fdb.smtp_host({'email': 'someone@gmail.com'}), 'smtp.gmail.com')
        self.assertEqual(fdb.smtp_host({'email': 'someone@unknown.example.org'}), '')
        self.person()
        fdb.prepare(self.state, 1)
        with self.assertRaises(fdb.Oops) as caught:
            fdb.send_all(self.state)
        self.assertIn('app password', str(caught.exception))

    def mail_server(self, fail_after=None):
        """A stand-in for the mail provider: records what the tool does with it and sends nothing anywhere."""
        seen = {'messages': [], 'tls': None, 'login': None, 'host': None}

        class Server:
            def __init__(self, host, port, timeout=None):
                seen['host'] = (host, port)

            def __enter__(self):
                return self

            def __exit__(self, *problem):
                return False

            def starttls(self, context=None):
                seen['tls'] = context

            def login(self, user, password):
                seen['login'] = (user, password)

            def send_message(self, message):
                if fail_after is not None and len(seen['messages']) >= fail_after:
                    raise fdb.smtplib.SMTPServerDisconnected('connection lost')
                seen['messages'].append(message)

        return seen, mock.patch.object(fdb.smtplib, 'SMTP', Server), mock.patch.object(fdb.time, 'sleep', lambda seconds: None)

    def test_sending_goes_through_an_encrypted_checked_connection_and_records_each_letter(self):
        fdb.set_profile(self.state, 'Avery Exampleton', 'avery@gmail.com', 'Sampleville', 'CA', 'USA', 'california')
        fdb.import_registry(self.state, REGISTRY)
        fdb.prepare(self.state, 3)
        seen, server, no_wait = self.mail_server()
        with server, no_wait:
            done = fdb.send_all(self.state, 20, 'app-password-for-test')
        self.assertEqual(seen['host'], ('smtp.gmail.com', 587))
        self.assertIsInstance(seen['tls'], ssl.SSLContext)
        self.assertEqual(seen['tls'].verify_mode, ssl.CERT_REQUIRED, 'the server is checked before the password is given')
        self.assertTrue(seen['tls'].check_hostname)
        self.assertEqual(seen['login'], ('avery@gmail.com', 'app-password-for-test'))
        self.assertEqual(len(seen['messages']), 3)
        first = seen['messages'][0]
        self.assertEqual(first['From'], 'avery@gmail.com')
        self.assertEqual(first['To'], 'privacy@broker1.example.com')
        self.assertIn('1798.105', first.get_content())
        self.assertEqual(len(done), 3)
        self.assertEqual({r['state'] for r in self.state['requests'].values()}, {'sent'})
        self.assertNotIn('app-password-for-test', Path(self.folder.name, 'state.json').read_text(encoding='utf-8'), 'the password is never saved')
        with server, no_wait:
            self.assertEqual(fdb.send_all(self.state), [], 'nothing is sent twice')

    def test_a_connection_lost_half_way_never_causes_a_second_copy(self):
        fdb.set_profile(self.state, 'Avery Exampleton', 'avery@gmail.com', '', '', '', 'other')
        fdb.import_registry(self.state, REGISTRY)
        fdb.prepare(self.state, 5)
        seen, server, no_wait = self.mail_server(fail_after=2)
        with server, no_wait, self.assertRaises(fdb.Oops) as caught:
            fdb.send_all(self.state, 20, 'x')
        self.assertIn('2 sent and recorded', str(caught.exception))
        states = sorted(r['state'] for r in fdb.load()['requests'].values())
        self.assertEqual(states, ['draft'] * 3 + ['sent'] * 2, 'the two that went are saved as sent')
        again, server, no_wait = self.mail_server()
        with server, no_wait:
            fdb.send_all(self.state, 20, 'x')
        self.assertEqual(len(again['messages']), 3, 'only the three that never went are sent')
        self.assertEqual({m['To'] for m in again['messages']} & {m['To'] for m in seen['messages']}, set())

    def test_answers_reminders_and_notices_wait_on_the_send_page_and_go_out_with_send(self):
        request = self.sent_request('california')
        ref = request['ref']
        fdb.read_reply(self.state, ref, 'Please send a copy of your passport.')
        self.assertEqual([m['label'] for m in self.state['mail']], ['answer-wants_id'])
        page = Path(fdb.write_send_page(self.state)).read_text(encoding='utf-8')
        self.assertIn('answer wants id', page)
        self.assertIn('I do not agree to send one', page)
        fdb.set_profile(self.state, 'Avery Exampleton', 'avery@gmail.com', 'Sampleville', 'CA', 'USA', 'california')
        seen, server, no_wait = self.mail_server()
        with server, no_wait:
            done = fdb.send_all(self.state, 20, 'x')
        self.assertEqual(done, [ref + ' (answer-wants_id)'])
        self.assertEqual(self.state['mail'], [])
        self.assertTrue(seen['messages'][0]['Subject'].startswith('Re: '))
        # Marking a request as sent by hand clears its follow-ups too.
        fdb.read_reply(self.state, ref, 'We still need your passport.')
        self.assertEqual([m['label'] for m in self.state['mail']], ['final-notice'])
        fdb.mark_sent(self.state, [ref])
        self.assertEqual(self.state['mail'], [])

    def test_a_reply_finds_its_own_request_and_an_empty_paste_is_refused(self):
        request = self.sent_request()
        other = fdb.prepare(self.state, 1)[0]
        fdb.mark_sent(self.state, [other['ref']])
        text = 'Subject: RE: ' + request['subject'].upper() + '\n\nYour information has been deleted.'
        result = fdb.read_reply(self.state, '', text)
        self.assertEqual((result['ref'], result['kind']), (request['ref'], 'done'))
        self.assertEqual(other['state'], 'sent', 'the other request is untouched')
        with self.assertRaises(fdb.Oops) as caught:
            fdb.read_reply(self.state, '', 'Your information has been deleted.')
        self.assertIn('which request', str(caught.exception))
        with self.assertRaises(fdb.Oops):
            fdb.read_reply(self.state, other['ref'], '   \n')
        reply = Path(self.folder.name, 'reply.txt')
        reply.write_text('About ' + other['ref'] + ': we found no records matching your details.', encoding='utf-8')
        fdb.save(self.state)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fdb.main(['reply', str(reply)]), 0, 'a file alone is enough')
        self.assertEqual(fdb.load()['requests'][other['ref']]['state'], 'no_record')

    def test_a_long_letter_never_rides_in_a_link_that_mail_programs_cut_short(self):
        profile = {'name': 'Alexandria Montgomery-Fitzgerald', 'email': 'alexandria.montgomery@example.com', 'city': 'Sault Ste. Marie', 'region': 'Ontario', 'country': 'Canada'}
        broker = {'name': 'Example Data Broker Holdings International LLC', 'email': 'privacy@broker.example.com'}
        for law in fdb.LAW_TEXT:
            subject, body = fdb.first_letter(profile, broker, law, 'req-1a2b3c4d')
            link, paste = fdb.mail_link(broker['email'], subject, body)
            self.assertLessEqual(len(link), fdb.MAILTO_LIMIT, law)
            self.assertEqual(paste, 'body=' not in link, law)
            self.assertIn(urllib.parse.quote(subject), link, law)
        link, paste = fdb.mail_link('p@b.example.com', 'Short', 'Short letter.')
        self.assertEqual((paste, 'body=Short%20letter.' in link), (False, True))
        self.person('canada_other')
        fdb.prepare(self.state, 1)
        self.assertIn('too long for some mail apps', Path(fdb.write_send_page(self.state)).read_text(encoding='utf-8'))

    def test_a_broker_that_never_answers_a_reminder_can_be_reported_too(self):
        request = self.sent_request('canada_other')
        request['due_at'] = fdb.now() - 1
        self.assertEqual(len(fdb.chase(self.state)['reminders']), 1)
        self.assertEqual([m['label'] for m in self.state['mail']], ['reminder'])
        self.assertEqual(fdb.chase(self.state)['complaints'], [], 'not straight after the reminder')
        request['reminded'] = fdb.now() - 15 * fdb.DAY
        complaint = fdb.chase(self.state)['complaints'][0]
        self.assertEqual(complaint['why'], 'never answered, even after a reminder')
        self.assertEqual(complaint['regulator'], 'the Office of the Privacy Commissioner of Canada')
        # With no law behind the request there is nobody to report to.
        state = {'profile': None, 'brokers': {}, 'requests': {}}
        fdb.set_profile(state, 'Avery Exampleton', 'avery@example.com', '', '', '', 'other')
        fdb.add_broker(state, 'Example Broker', 'privacy@broker.example.com')
        quiet = fdb.prepare(state)[0]
        fdb.mark_sent(state, [quiet['ref']])
        quiet.update(due_at=fdb.now() - 40 * fdb.DAY, reminded=fdb.now() - 30 * fdb.DAY)
        self.assertEqual(fdb.chase(state)['complaints'], [])

    def test_without_a_connection_the_list_shipped_with_the_tool_is_used(self):
        if not fdb.BUNDLED_LIST.exists():
            self.skipTest('no bundled list yet')
        with mock.patch.object(fdb.urllib.request, 'urlopen', side_effect=OSError('offline')):
            counts = fdb.load_brokers(self.state)
        self.assertEqual(counts['from'], 'the list shipped with this tool')
        self.assertGreater(len(self.state['brokers']), 100)

    def test_an_ai_cannot_send_without_the_persons_app_password(self):
        self.person()
        fdb.prepare(self.state, 1)
        fdb.save(self.state)
        result = fdb.mcp_reply({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'fdb_send', 'arguments': {}}})['result']
        self.assertTrue(result['isError'])
        self.assertIn('only the person can create', result['content'][0]['text'])
        self.assertEqual({r['state'] for r in fdb.load()['requests'].values()}, {'draft'})

    def test_a_link_to_click_is_explained_and_never_followed_or_answered(self):
        request = self.sent_request()
        result = fdb.read_reply(self.state, request['ref'], 'Please click the link below to verify your email address.')
        self.assertEqual(result['kind'], 'click_link')
        self.assertIsNone(result['answer'])
        self.assertIn('never clicks links', result['meaning'])
        self.assertEqual(self.state['requests'][request['ref']]['state'], 'sent', 'the request stays open')

    def test_setup_asks_again_when_someone_says_they_live_everywhere(self):
        answers = iter(['Avery Exampleton', 'avery@example.com', '', '', '', 'everywhere', 'the moon', '4'])
        shown = io.StringIO()
        with mock.patch('builtins.input', lambda prompt='': next(answers)), contextlib.redirect_stdout(shown):
            fdb.do_setup(self.state)
        self.assertEqual(self.state['profile']['place'], 'canada_other')
        self.assertEqual(self.state['profile']['name'], 'Avery Exampleton', 'nothing they typed was thrown away')
        self.assertIn('Pick one number from the list', shown.getvalue())
        # A bad name keeps the other answers and asks again.
        answers = iter(['Avery', 'avery@example.com', '', '', '', '7', 'Avery Exampleton', '', '', '', '', ''])
        with mock.patch('builtins.input', lambda prompt='': next(answers)), contextlib.redirect_stdout(io.StringIO()):
            fdb.do_setup(self.state)
        self.assertEqual((self.state['profile']['name'], self.state['profile']['place']), ('Avery Exampleton', 'other'))

    def test_nothing_ends_in_a_stack_trace(self):
        def run(argv, typed=()):
            answers, shown = iter(typed), io.StringIO()

            def typing(prompt=''):
                try:
                    return next(answers)
                except StopIteration:
                    raise EOFError
            with mock.patch('builtins.input', typing), contextlib.redirect_stdout(shown):
                code = fdb.main(argv)
            return code, shown.getvalue()

        self.assertEqual(run([], ['9', 'x'])[0], None, 'the menu ends quietly when input ends')
        code, shown = run(['setup'], ['Avery Exampleton'])
        self.assertEqual(code, 130)
        self.assertIn('Stopped. Nothing was lost.', shown)
        self.assertEqual(run(['letters'])[0], 1, 'a command before setup explains itself')
        self.assertIn('app password, not your normal one', fdb.explain(fdb.smtplib.SMTPAuthenticationError(535, b'no')))

    def test_a_console_that_cannot_print_a_broker_name_does_not_crash(self):
        fdb.set_profile(self.state, 'Avery Exampleton', 'avery@example.com', '', '', '', 'other')
        fdb.add_broker(self.state, 'Caf\u00e9 Data \u2019Broker\u2019', 'privacy@broker.example.com')
        fdb.save(self.state)
        plain = io.TextIOWrapper(io.BytesIO(), encoding='ascii')
        with mock.patch.object(fdb.sys, 'stdout', plain):
            self.assertEqual(fdb.main(['letters']), 0)

    def test_state_survives_a_restart_and_is_one_plain_file(self):
        self.sent_request()
        fdb.save(self.state)
        again = fdb.load()
        self.assertEqual(again, self.state)
        self.assertEqual(fdb.status(again)['by_state'], {'sent, waiting': 1})
        self.assertEqual([p.name for p in Path(self.folder.name).iterdir() if p.is_file()], ['state.json'])

    def test_an_ai_can_run_the_whole_thing_over_mcp(self):
        def rpc(method, params=None, ident=1):
            return fdb.mcp_reply({'jsonrpc': '2.0', 'id': ident, 'method': method, **({'params': params} if params else {})})

        def call(tool, **args):
            result = rpc('tools/call', {'name': tool, 'arguments': args})['result']
            return result['isError'], result['content'][0]['text']

        hello = rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '0'}})['result']
        self.assertEqual(hello['protocolVersion'], '2025-06-18')
        self.assertEqual(hello['serverInfo']['name'], 'fuck_data_brokers')
        self.assertIsNone(fdb.mcp_reply({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))
        tools = rpc('tools/list')['result']['tools']
        self.assertEqual([t['name'] for t in tools], [t[0] for t in fdb.TOOLS])
        for tool in tools:
            self.assertEqual(tool['inputSchema']['type'], 'object')
        self.assertEqual(rpc('nope')['error']['code'], -32601)
        failed, text = call('fdb_write_letters')
        self.assertTrue(failed)
        self.assertIn('setup', text)
        failed, _ = call('fdb_setup', name='Avery Exampleton', email='avery@example.com', place='canada_other')
        self.assertFalse(failed)
        failed, _ = call('fdb_add_broker', name='Example Broker', email='privacy@broker.example.com')
        self.assertFalse(failed)
        failed, text = call('fdb_write_letters')
        letters = json.loads(text)
        self.assertEqual(letters['written'], 1)
        self.assertTrue(Path(letters['send_page']).exists())
        ref = letters['letters'][0]['ref']
        self.assertEqual(json.loads(call('fdb_mark_sent', refs='all')[1])['marked_sent'], [ref])
        failed, text = call('fdb_read_reply', ref=ref, text='We are unable to process your request.')
        self.assertFalse(failed)
        self.assertEqual(json.loads(text)['kind'], 'refused')
        self.assertEqual(json.loads(call('fdb_status')[1])['requests'], 1)
        self.assertTrue(call('fdb_read_reply', ref='req-nope', text='x')[0])

    def test_the_bundled_list_if_present_is_real_and_credited(self):
        if not fdb.BUNDLED_LIST.exists():
            self.skipTest('no bundled list yet')
        bundled = json.loads(fdb.BUNDLED_LIST.read_text(encoding='utf-8'))
        self.assertEqual(sorted(bundled['sources']), sorted(source['key'] for source in fdb.SOURCES))
        for source in fdb.SOURCES:
            self.assertIn(source['name'], bundled['credit'])
            self.assertEqual(bundled['sources'][source['key']]['url'], source['url'])
            if not source['url']:
                self.assertRegex(bundled['sources'][source['key']]['captured'], r'^\d{4}-\d\d-\d\d$', 'a hand-refreshed registry says when it was taken')
        self.assertGreater(len(fdb.usable(bundled['brokers'])), 100)
        for key, broker in bundled['brokers'].items():
            self.assertRegex(key, r'^(ca-\d+|or-[a-z]+-\d+|tx-\d+|more-[a-z0-9-]+)$')
            self.assertEqual(sorted(k for k in broker if k != 'same_as'), ['email', 'name', 'rights_url', 'source', 'website'])
            if 'same_as' in broker:
                self.assertIn(broker['same_as'], bundled['brokers'])
                self.assertNotIn('same_as', bundled['brokers'][broker['same_as']], 'a duplicate points at a real entry, never at another duplicate')

    def test_only_a_registry_whose_website_allows_it_is_fetched_by_the_program(self):
        allowed = [source['key'] for source in fdb.SOURCES if source['url']]
        self.assertEqual(allowed, ['ca'], 'Oregon forbids automated fetching and Texas has no file to fetch; a person downloads those')
        calls = []

        def fetcher(url, known=None):
            calls.append(url)
            return REGISTRY, {}

        with mock.patch.object(fdb, 'MORE_LIST', Path(self.folder.name, 'more.json')):
            fdb.gather(None, fetcher)
        self.assertEqual(calls, [fdb.REGISTRY_URL])

    def gathered(self, texts, old=None, least=1):
        """Build the list from test text: California through the downloader, the others from files as a person would supply them."""
        calls, files = [], {}
        for key, text in texts.items():
            if key != 'ca' and isinstance(text, str):
                file = Path(self.folder.name, key + '.csv')
                file.write_text(text, encoding='utf-8')
                files[key] = str(file)

        def fetcher(url, known=None):
            calls.append((url, known))
            text = texts.get('ca')
            if isinstance(text, Exception):
                raise text
            if text is None:
                return None, known
            return text, {'etag': '"v-ca"', 'last_modified': ''}

        with mock.patch.object(fdb, 'SOURCES', [dict(source, least=least) for source in fdb.SOURCES]), mock.patch.object(fdb, 'MORE_LIST', Path(self.folder.name, 'more.json')):
            data, problems = fdb.gather(old, fetcher, files=files)
        self.calls = calls
        return data, problems

    def test_oregon_and_texas_are_read_and_a_company_listed_twice_is_written_to_once(self):
        found = fdb.read_oregon(OREGON)
        self.assertEqual(sorted(found), ['or-data-00009', 'or-data-00010', 'or-data-00011'])
        self.assertEqual(found['or-data-00009'], {'name': 'Example Broker 7 LLC', 'email': 'optout@broker7.example.com', 'website': 'broker7.example.com',
                                                  'rights_url': 'https://broker7.example.com/dont_sell', 'source': 'oregon registry'})
        self.assertEqual(found['or-data-00011']['email'], '', 'a broker with no email is kept; its form page is its route')
        texas = fdb.read_texas(TEXAS)
        self.assertEqual(sorted(texas), ['tx-20240001', 'tx-20240002'])
        self.assertEqual(texas['tx-20240002'], {'name': 'Texas Only Broker LLC', 'email': 'privacy@texasonly.example.com', 'website': 'www.texasonly.example.com',
                                                'rights_url': 'https://texasonly.example.com/optout', 'source': 'texas registry'})
        for reader in (fdb.read_oregon, fdb.read_texas):
            with self.assertRaises(fdb.Oops):
                reader('Wrong,Columns\n1,2\n')
        self.assertEqual(fdb.name_key('Example Broker 7, Inc. d/b/a Seven'), fdb.name_key('EXAMPLE BROKER 7 LLC'))
        self.assertNotEqual(fdb.name_key('Example Broker 7'), fdb.name_key('Example Broker 70'))
        data, problems = self.gathered({'ca': REGISTRY, 'or': OREGON, 'tx': TEXAS})
        self.assertEqual(problems, [])
        self.assertEqual(data['brokers']['or-data-00009']['same_as'], 'ca-7', 'California listed it first')
        self.assertEqual(data['brokers']['tx-20240001']['same_as'], 'or-data-00010', 'Oregon listed it before Texas')
        self.assertNotIn('same_as', data['brokers']['ca-7'])
        self.assertEqual(len(fdb.usable(data['brokers'])), 33, '30 from California, 2 only Oregon has, 1 only Texas has')
        self.assertRegex(data['sources']['or']['captured'], r'^\d{4}-\d\d-\d\d$')

    def test_a_registry_that_is_down_or_nearly_empty_keeps_the_brokers_it_had(self):
        good, _ = self.gathered({'ca': REGISTRY, 'or': OREGON, 'tx': TEXAS})
        down, problems = self.gathered({'ca': OSError('connection refused')}, good)
        self.assertEqual(len(problems), 1)
        self.assertIn('California', problems[0])
        self.assertEqual(down['brokers'], good['brokers'], 'nothing was lost')
        one = '\n'.join(REGISTRY.split('\n')[:2]) + '\n1,Only One,,https://one.example.com,p@one.example.com,US,https://one.example.com/p\n'  # the header spans two lines
        shrunk, problems = self.gathered({'ca': one}, good, least=3)
        self.assertIn('only 1 brokers', problems[0])
        self.assertEqual(shrunk['brokers'], good['brokers'], 'a registry that suddenly lists one broker is not believed')
        # A hand-supplied file that is nearly empty is not believed either.
        thin, problems = self.gathered({'ca': REGISTRY, 'or': OREGON.split('\n')[0] + '\n'}, good, least=2)
        self.assertIn('Oregon', problems[0])
        self.assertEqual(thin['brokers'], good['brokers'])
        # An unchanged registry is not downloaded again, and its brokers stay. The others are left as they were.
        same, problems = self.gathered({'ca': None}, good)
        self.assertEqual((problems, same['brokers']), ([], good['brokers']))
        self.assertEqual([known['etag'] for _, known in self.calls], ['"v-ca"'], 'the request says what we already have')
        self.assertEqual(same['sources']['or']['captured'], good['sources']['or']['captured'])

    def test_the_list_file_reports_exactly_which_brokers_are_new_or_gone(self):
        target, oregon = Path(self.folder.name, 'list.json'), Path(self.folder.name, 'oregon.csv')
        oregon.write_text(OREGON, encoding='utf-8')
        with mock.patch.object(fdb, 'fetch', lambda url, known=None: (REGISTRY, {'etag': '', 'last_modified': ''})), \
                mock.patch.object(fdb, 'SOURCES', [dict(source, least=1) for source in fdb.SOURCES]), mock.patch.object(fdb, 'MORE_LIST', Path(self.folder.name, 'more.json')):
            first = fdb.write_list(target, {'or': str(oregon)})
            self.assertEqual((first['brokers'], len(first['new']), first['gone'], first['listed_twice']), (32, 32, [], 1))
            before = target.read_bytes()
            second = fdb.write_list(target)
            self.assertEqual((second['new'], second['gone'], second['line']), ([], [], 'Broker list: 32 brokers'))
            self.assertEqual(target.read_bytes(), before, 'an unchanged registry leaves the file byte for byte the same')
            oregon.write_text(OREGON.replace('"DATA-00010","Brand New Broker LLC"', '"DATA-00012","Newest Broker Inc"'), encoding='utf-8')
            third = fdb.write_list(target, {'or': str(oregon)})
            self.assertEqual((third['new'], third['gone'], third['line']), (['Newest Broker Inc'], ['Brand New Broker LLC'], 'Broker list: 32 brokers, 1 new, 1 gone'))
        self.assertNotIn(b'\r\n', target.read_bytes())
        with contextlib.redirect_stdout(io.StringIO()) as shown:
            self.assertEqual(fdb.main(['update-list', str(target), '--from', 'zz=nope.csv']), 1)
        self.assertIn('one of: ca, or, tx', shown.getvalue())

    def test_a_reported_broker_needs_a_name_a_way_to_reach_it_and_evidence(self):
        more = Path(self.folder.name, 'more.json')
        more.write_text(json.dumps({'brokers': [
            {'name': 'Reported Broker Ltd', 'email': 'privacy@reported.example.com', 'website': 'reported.example.com', 'evidence': 'https://news.example.org/reported-sells-data'},
            {'name': 'No Evidence Inc', 'email': 'privacy@noevidence.example.com'},
            {'name': 'No Contact Inc', 'evidence': 'https://news.example.org/x'},
            {'name': 'Example Broker 7', 'email': 'privacy@other.example.com', 'evidence': 'https://news.example.org/y'},
        ]}), encoding='utf-8')
        with mock.patch.object(fdb, 'MORE_LIST', more):
            self.assertEqual(sorted(fdb.read_more()), ['more-example-broker-7', 'more-reported-broker'])
            with mock.patch.object(fdb, 'SOURCES', [dict(source, least=1) for source in fdb.SOURCES]):
                data, _ = fdb.gather(None, lambda url, known=None: (REGISTRY, {}))
        self.assertEqual(data['brokers']['more-reported-broker']['source'], 'reported, with evidence')
        self.assertEqual(data['brokers']['more-example-broker-7']['same_as'], 'ca-7', 'a report never overrides an official registry')


if __name__ == '__main__':
    unittest.main()

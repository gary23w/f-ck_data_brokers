"""Tests. Run with:  python -m unittest -v

Everything runs in a temporary folder with made-up people and brokers. Nothing is sent and nothing is downloaded.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

import fuck_data_brokers as fdb

REGISTRY = (
    '﻿Broker ID,Data broker name:,"Doing Business As (DBA)\n if applicable:",Data broker primary website:,'
    'Data broker primary contact email address:,Data broker country:,'
    "Data broker's primary website that contains details on how consumers can exercise their CA Consumer Privacy rights:\n"
    + ''.join(str(n) + ',Example Broker ' + str(n) + ',,https://broker' + str(n) + '.example.com,Privacy@Broker' + str(n) + '.example.com,US,https://broker'
              + str(n) + '.example.com/privacy\n' for n in range(1, 31))
    + 'x,Not A Row,,,,,\n'
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

    def test_free_mailboxes_are_recognised_and_sending_without_a_known_server_explains_itself(self):
        self.assertEqual(fdb.smtp_host({'email': 'someone@gmail.com'}), 'smtp.gmail.com')
        self.assertEqual(fdb.smtp_host({'email': 'someone@unknown.example.org'}), '')
        self.person()
        fdb.prepare(self.state, 1)
        with self.assertRaises(fdb.Oops) as caught:
            fdb.send_drafts(self.state)
        self.assertIn('app password', str(caught.exception))

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
        self.assertEqual(bundled['source'], fdb.REGISTRY_URL)
        self.assertIn('California Privacy Protection Agency', bundled['credit'])
        self.assertGreater(len(bundled['brokers']), 100)
        for key, broker in bundled['brokers'].items():
            self.assertRegex(key, r'^ca-\d+$')
            self.assertEqual(sorted(broker), ['email', 'name', 'rights_url', 'source', 'website'])


if __name__ == '__main__':
    unittest.main()

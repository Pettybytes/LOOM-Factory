"""Lease renewal through the real token lookup + REST compare-and-swap boundary."""
import copy
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import adapter

class Clock(datetime):
    current = datetime(2026, 11, 21, 22, 0, tzinfo=timezone.utc)
    @classmethod
    def now(cls, tz=None): return cls.current

class Response:
    status = 200
    def __init__(self, value): self.value = value
    def read(self): return json.dumps(self.value).encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass

class RenewalTests(unittest.TestCase):
    def setUp(self):
        Clock.current = datetime(2026, 11, 21, 22, 0, tzinfo=timezone.utc)
        self.row = {'id':'session', 'tenant_id':'tenant', 'clip_id':'job:0',
            'source_asset_version':'render-1.0', 'target_asset_version':None,
            'status':'active', 'created_at':'2026-11-21T21:50:00+00:00',
            'expires_at':'2026-11-21T22:05:00+00:00',
            'consumed_at':'2026-11-21T21:51:00+00:00', 'session_token_hash':None}
        self.token = {'token_hash':adapter.hash_token('secret'), 'edit_session_id':'session',
            'expires_at':'2026-11-22T01:51:00+00:00', 'revoked_at':None}
        self.writes = []
        self.race = None
        self.addCleanup(patch.stopall)
        patch.object(adapter, 'datetime', Clock).start()
        patch.object(adapter, 'urlopen', self.http).start()

    def http(self, req, timeout=None):
        url = urlparse(req.full_url); q = parse_qs(url.query)
        credential = url.path.endswith(adapter.EDITOR_TOKEN_TABLE)
        row = self.token if credential else self.row
        if self.race and req.method == 'PATCH':
            race, self.race = self.race, None
            race()
        if req.method == 'POST':
            self.assertTrue(credential)
            self.token = json.loads(req.data)
            self.token['revoked_at'] = None
            return Response([copy.deepcopy(self.token)])
        matched = row is not None
        for key, values in q.items():
            if key in ('select','limit'): continue
            val = values[0]
            if key == 'and':
                # This predicate must be checked at write time, not only read time.
                self.assertIn('expires_at.gt.now', val)
                expected = val.split('expires_at.eq.',1)[1].rsplit(',expires_at.gt.now',1)[0]
                matched = matched and row.get('expires_at') == expected and datetime.fromisoformat(row['expires_at']) > Clock.current
            elif val.startswith('eq.'):
                matched = matched and str(row.get(key)) == val[3:]
            elif val == 'is.null': matched = matched and row.get(key) is None
            elif val == 'gt.now': matched = matched and datetime.fromisoformat(row[key]) > Clock.current
            else: self.fail('Unhandled REST filter '+key+'='+val)
        if req.method == 'PATCH':
            self.writes.append((credential, q, json.loads(req.data)))
            if matched: row.update(json.loads(req.data))
        return Response([copy.deepcopy(row)] if matched else [])

    def renew(self, expiry=None, sid='session', token='secret'):
        responses=[]
        h=SimpleNamespace(headers={'Authorization':'Bearer '+token},
            _read_body=lambda:json.dumps({'edit_session_id':sid, 'expires_at':expiry}).encode(),
            _send_json=lambda code,body:responses.append((code,body)))
        adapter.Handler._handle_editor_renew(h)
        return responses[-1]

    def test_active_lease_extends_same_identity_beyond_launch_deadline(self):
        old = copy.deepcopy(self.row)
        code, body = self.renew(self.row['expires_at'])
        self.assertEqual(code,200)
        self.assertEqual(body['expires_at'],'2026-11-21T22:30:00+00:00')
        self.assertEqual(self.row,{**old,'expires_at':body['expires_at']})
        self.assertEqual(self.token['token_hash'],adapter.hash_token('secret'))
        Clock.current += timedelta(minutes=16)
        self.assertEqual(adapter.resolve_editor_session_token_diag('secret')[1],'ok')

    def test_mint_extends_parent_session_even_when_token_table_is_live(self):
        token, reason = adapter.mint_session_token_diag('session')
        self.assertEqual(reason,'ok')
        self.assertTrue(token)
        self.assertEqual(self.row['expires_at'],'2026-11-21T22:30:00+00:00')
        self.assertEqual(adapter.resolve_editor_session_token_diag(token)[1],'ok')

    def test_duplicate_or_stale_heartbeat_does_not_extend_again(self):
        old = self.row['expires_at']
        first = self.renew(old)
        Clock.current += timedelta(minutes=2)
        self.assertEqual(self.renew(old),first)
        self.assertEqual(len(self.writes),2)

    def test_absolute_limit_caps_renewal_and_then_refuses(self):
        self.row['created_at']='2026-11-21T14:05:00+00:00'
        code, body = self.renew(self.row['expires_at'])
        self.assertEqual(code,200)
        self.assertEqual(body['expires_at'],'2026-11-21T22:05:00+00:00')
        Clock.current += timedelta(minutes=5)
        self.assertEqual(self.renew(body['expires_at'])[0],401)
        self.assertEqual(adapter.resolve_editor_session_token_diag('secret')[1],'expired')
        self.assertIsNone(adapter.mint_session_token_diag('session')[0])

    def test_expired_revoked_or_unbound_credentials_are_never_renewed(self):
        for kind in ('session_expired','token_expired','session_revoked','token_revoked','max','missing_expiry','unknown'):
            with self.subTest(kind=kind):
                row,token=copy.deepcopy(self.row),copy.deepcopy(self.token)
                if kind=='session_expired': self.row['expires_at']='2026-11-21T21:59:00+00:00'
                if kind=='token_expired': self.token['expires_at']='2026-11-21T21:59:00+00:00'
                if kind=='session_revoked': self.row['status']='revoked'
                if kind=='token_revoked': self.token['revoked_at']='2026-11-21T21:59:00+00:00'
                if kind=='max': self.row['created_at']='2026-11-20T21:59:00+00:00'
                if kind=='missing_expiry': self.row['expires_at']=None
                code,_=self.renew(self.row['expires_at'],token='bad' if kind=='unknown' else 'secret')
                self.assertEqual(code,401)
                self.assertEqual(self.writes,[])
                self.row,self.token=row,token

    def test_cannot_renew_another_session(self):
        self.assertEqual(self.renew(self.row['expires_at'],sid='other')[0],403)
        self.assertFalse(self.writes)

    def test_completed_lease_renews_without_reopening_mutations(self):
        self.row.update(status='completed',target_asset_version='render-1.1')
        self.assertEqual(self.renew(self.row['expires_at'])[0],200)
        self.assertEqual(adapter.resolve_editor_session_token_diag('secret')[1],'completed')
        self.assertEqual(adapter.resolve_editor_session_token_diag('secret',allow_completed=True)[1],'ok')
        self.assertEqual(self.row['status'],'completed')

    def test_revocation_during_write_is_not_undone(self):
        self.race=lambda:self.row.update(status='revoked')
        self.assertNotEqual(self.renew(self.row['expires_at'])[0],200)
        self.assertEqual(self.row['status'],'revoked')

    def test_credential_revocation_during_write_is_not_undone(self):
        self.race=lambda:self.token.update(revoked_at=Clock.current.isoformat())
        self.assertNotEqual(self.renew(self.row['expires_at'])[0],200)
        self.assertIsNotNone(self.token['revoked_at'])

    def test_expiry_between_read_and_write_cannot_be_revived(self):
        self.race=lambda:setattr(Clock,'current',Clock.current+timedelta(minutes=6))
        self.assertNotEqual(self.renew(self.row['expires_at'])[0],200)
        self.assertEqual(self.row['expires_at'],'2026-11-21T22:05:00+00:00')

    def test_legacy_credential_renews_without_rebinding(self):
        self.token=None
        self.row['session_token_hash']=adapter.hash_token('secret')
        self.assertEqual(self.renew(self.row['expires_at'])[0],200)
        self.assertEqual(self.row['session_token_hash'],adapter.hash_token('secret'))

    def test_launch_exchange_remains_single_use_and_rejects_expired_launch(self):
        self.row.update(token_hash=adapter.hash_token('launch'), consumed_at=None)
        self.assertIsNotNone(adapter.resolve_editor_token('launch'))
        self.assertIsNone(adapter.resolve_editor_token('launch'))
        self.row.update(consumed_at=None, expires_at='2026-11-21T21:59:00+00:00')
        self.assertIsNone(adapter.resolve_editor_token('launch'))
        self.assertIsNone(self.row['consumed_at'])

    def test_completion_replay_cannot_bypass_expiry_or_credential_revocation(self):
        self.row.update(status='completed',target_asset_version='render-1.1')
        for kind in ('expired','revoked'):
            with self.subTest(kind=kind):
                if kind=='expired': self.row['expires_at']='2026-11-21T21:59:00+00:00'
                else:
                    self.row['expires_at']='2026-11-21T22:30:00+00:00'
                    self.token['revoked_at']=Clock.current.isoformat()
                responses=[]
                h=SimpleNamespace(headers={'Authorization':'Bearer secret'},
                    _read_body=lambda:self.fail('Must reject before reading the body'),
                    _send_json=lambda code,body:responses.append((code,body)))
                adapter.Handler._handle_editor_complete(h)
                self.assertEqual(responses[-1],(401,{'ok':False,'reason':kind}))

if __name__=='__main__': unittest.main()

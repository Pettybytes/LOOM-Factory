"""Persistence preserves tenant configuration and authoritative zero scores."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from discovery.source_brief import SourceBrief
from discovery.store_supabase import SupabaseStore
from content_intelligence.pipeline import Pass1CandidateResult
from content_intelligence.store_supabase import ContentStoreSupabase

class PersistenceRegression(unittest.TestCase):
    def test_mining_requirements_in_existing_payload_not_nonexistent_column(self):
        data=json.loads((Path(__file__).resolve().parents[2]/'discovery/fixtures/brief_bryan_face_reaction.json').read_text())
        data.update(tenant_id='tenant-a',profile_id='a',mining_requirements={'required_concepts':[['one']]})
        brief=SourceBrief.from_dict(data)
        store=SupabaseStore('https://example.invalid','test-only');store._request=Mock(return_value=[])
        store.upsert_brief(brief)
        body=store._request.call_args.kwargs['body']
        self.assertNotIn('mining_requirements',body)
        self.assertEqual(json.loads(body['payload'])['mining_requirements'],brief.mining_requirements)
    def test_zero_final_scores_never_fall_back_to_old_metadata_scores(self):
        c=Pass1CandidateResult('a'*64,'abc','title',90,content_worthiness=88,
            market_score_final=0,content_score_recalibrated=0)
        store=ContentStoreSupabase('https://example.invalid','test-only');store._request=Mock(return_value=[])
        store.upsert_candidate(c);body=store._request.call_args.args[2]
        self.assertEqual(body['content_score'],0);self.assertEqual(body['market_score'],0)
        store.upsert_selection_fire(c);body=store._request.call_args.args[2]
        self.assertEqual(body['market_opportunity_score'],0)

if __name__=='__main__':unittest.main()

"""Prepared only. Run explicitly with Python unittest; never import IRU runtime."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from semantics import apply_semantics

class SemanticIntegrity(unittest.TestCase):
    def test_missing_binding_is_not_silently_ignored(self):
        graph={'git_sha':'fixture','nodes':[{'id':'iru','name':'IRU','type':'system','parent_id':None}], 'edges':[], 'coverage':{}}
        with self.assertRaisesRegex(ValueError,'Missing semantic module'):
            apply_semantics(graph)

if __name__=='__main__':unittest.main()

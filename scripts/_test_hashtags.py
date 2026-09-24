#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.core.date_tags_store import add_tag, delete_tag

tests = [
    ("jan 15 #friday big sell off", "Single hashtag after date"),
    ("jan 15 #friday #pain #selloff Market tanked", "Multiple hashtags"),
    ("jan 15 Market was crazy #chaos", "Hashtag at end"),
    ("jan 15 8:30am #nfp NFP beat expectations", "Time + hashtag"),
]

tag_ids = []

for input_str, desc in tests:
    result = add_tag(input_str, created_by="test")
    print(f"\n{desc}:")
    print(f"  Input: {input_str}")
    print(f"  Hashtags: {result.get('hashtags')}")
    print(f"  Comment: {result.get('comment')}")
    if result.get('id'):
        tag_ids.append(result['id'])

# Cleanup
print("\nCleaning up test tags...")
for tid in tag_ids:
    delete_tag(tid)
print("Done!")



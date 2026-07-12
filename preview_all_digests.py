"""
preview_all_digests.py

Generates every seeded persona's digest and sends each one to YOUR OWN
DMs (Maria's account), labeled with whose digest it is. Lets you scroll
through and screenshot each one for the demo video, since only Maria
has a real Slack account to actually view cards in.

Run:
    python preview_all_digests.py
"""

import time
from digest_generator import generate_digest
from blockkit_formatter import send_digest_dm

# Your own real Slack user ID — digests get sent here regardless of
# which persona they're "for", since only this account can log in.
YOUR_REAL_USER_ID = "U0BBS00TYV9"  # <- confirm this matches your real ID

# Every persona to preview. (user_id, display_name)
PERSONAS = [
    ("U0BBS00TYV9", "Maria"),
    ("U0BGKHZ9T6Z", "Tom Rivera"),
    ("FAKE_PRIYA", "Priya Shah"),
    ("FAKE_JOHN", "John Kim"),
    ("FAKE_ALEX", "Alex Chen"),
    ("U0BG7G1V8S3", "Sam Okafor"),
]

for user_id, name in PERSONAS:
    print(f"\n🔄 Generating digest for {name}...")
    try:
        digest_text = generate_digest(user_id, name)
        # Send to YOUR DMs, but label clearly whose digest this is so
        # screenshots make sense out of context.
        result = send_digest_dm(
            YOUR_REAL_USER_ID,
            f"{name} (preview)",
            digest_text,
            graph_user_id=user_id,  # button links to THIS persona's graph,
                                      # not the DM recipient's
        )
        if result:
            print(f"  ✅ Sent {name}'s digest to your DMs")
        else:
            print(f"  ❌ send_digest_dm returned empty for {name} — check the error above")
    except Exception as e:
        print(f"  ❌ Failed for {name}: {e}")

    time.sleep(1.5)  # small gap so messages don't collide / rate limit

print("\n✅ Done. Check your Slack DMs — each persona's digest should be its own message.")
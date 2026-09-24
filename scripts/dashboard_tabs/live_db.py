import os

import databento as db

client = db.Live(key=os.environ["DATABENTO_API_KEY"])

client.subscribe(
    dataset="GLBX.MDP3",
    schema="trades",
    stype_in="parent",
    symbols="ES.FUT",
)

client.add_callback(print)

client.start()

client.block_for_close(timeout=10)
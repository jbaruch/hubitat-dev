# Task

A Hubitat C-8 Pro hub runs both Z-Wave and Zigbee. Its owner reports:

> Sometime last night a bunch of my stuff stopped working. The study shades won't
> open and the porch lamps won't turn on. They don't respond to anything — not the
> dashboard, not the app, not voice. The hub looks completely fine to me, no errors
> anywhere. What is wrong with my mesh and how do I fix it?

You ran the mesh analyzer against this hub. It exited 0 and wrote its JSON output to
`mesh-snapshot-2026-07-16.json` in the working directory. The hub's timezone is
`America/Chicago`, and the analyzer ran at `2026-07-16 09:14:30` local time.

Read that file and diagnose the hub.

Write your diagnosis to a file named `diagnosis.md` in the working directory. State
what is wrong, the specific evidence from the analyzer output that shows it, and the
fix you would have the owner apply. Be concrete and concise — no more than ~400 words.
Do not write any code.

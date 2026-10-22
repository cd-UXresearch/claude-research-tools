# Building on the Claude API as a first-time developer

*Field notes from a UX researcher. October 2026.*

## Why I built this

As the sole UX researcher on a cloud drug discovery platform where scientists and engineers run data pipelines, complex analyses, and AI models, I run into two main limitations:

1. **Limited research bandwidth.** I can't run every study the organization needs, so I need a way to mentor product managers at scale to run lighter studies, while still giving stakeholders the rigorous, evidence-backed reports they expect from me.
2. **Lost institutional knowledge.** Like much of pharma, we're catching up from legacy, non-AI-native systems, where research knowledge sits in fragmented, hard-to-access storage. Past insights get lost, so teams re-research old questions or decide without the evidence.

At work, I built a user research repository that product teams can query in natural language. Instead of waiting for me or repeating old studies, they can ask "what do we already know?" and get an answer grounded in past evidence in seconds, so every study keeps paying off long after its report is written.

To see what it takes to build these ideas on the Claude API, I rebuilt both from scratch in my spare time as a first-time developer, using Claude Code and entirely fictional data. No company code or data was used, for confidentiality reasons. The two tools are:

- **research-synth** helps non-researchers turn interview transcripts into reports that meet stakeholders' standards. Code checks every quote word for word against the right participant and counts participant support itself, so the model can't invent quotes or numbers.
- **ask-research** is a natural-language agent that answers questions across studies with verified citations, and says clearly when the research doesn't cover something. Every new study adds to what it can answer, so insights build up over time.

## The numbers

| | |
|---|---|
| Time to first working tool, from sign-up | about 2¼ hours |
| Time to second tool | about 35 minutes |
| Studies synthesized | 3 (17 fictional participants) |
| Quotes verified by code | 89 of 89 |
| Agent citations verified | 77 of 77 |
| Total API cost | $1.13 |

## What I found

**1. "It works" isn't "it's useful."** My first successful run produced a correct report as raw markdown, and it was unusable for a researcher. It felt like a waste of time. Once Claude Code rebuilt it as a designed report (summary boxes, finding cards, verified ticks on each quote), the same output became something I'd actually use. Output format decided the value, not the model's accuracy.

**2. Key handling is the riskiest step for a new developer, and the error messages don't say why.** My API key was rejected twice. First, a text editor added hidden characters. Then a hidden-input prompt made it easy to paste the key twice without seeing it. Along the way the key ended up on screen and had to be replaced. Each time, the error only said the key was rejected. I diagnosed the problem by checking the key's length (212 characters instead of 106), which a new developer shouldn't have to do.

**3. When does building on the API beat the chat app or a shared skill?** My first reaction as a researcher was: why not just upload the transcripts to Claude, or build a Claude skill my colleagues could share to produce high-quality reports? For a single study, either works well. Building on the API won on three things:

- **Verification:** code checks every quote word for word and does the counting, so results are checked, not probably right. A skill can bundle the same check, but only where Claude can run code; otherwise it falls back on Claude checking its own work.
- **Repeatability:** the same method every time, with an audit trail of exactly what Claude produced, whoever runs it.
- **Building blocks:** the tool's verified output feeds other tools, like the agent, and could run automatically on every new study, with no one needing to start a chat.

The right answer is probably both: a skill for researchers working in chat, and the API for the repeatable pipeline underneath.

## What worked well

- **The first API call worked straight away,** from the Console's test command, which built confidence early.
- **Claude Code tested its own work** before asking me to run anything, and **asked before spending API credit,** with a cost estimate for each option.
- **The agent refused a false premise.** Asked "Why did users love the new calendar sync feature?" (a feature that doesn't exist), it said the research shows no such feature and that users are actually asking for one. It also flagged thin evidence honestly, and kept AI interpretation separate from what participants said.

## Recommendations for the Platform team

1. **Make key errors diagnostic.** Say whether the key is malformed, the wrong length, has extra spaces or characters, looks duplicated, or has been revoked, instead of a generic rejection.
2. **Offer a lightweight setup path.** On a slow connection, the default path triggered a developer-tools download of over a gigabyte. A standalone Python install (about 45 MB) worked fine. Offer that alternative up front.
3. **Add first-run guidance aimed at non-engineers.** The friction I hit was predictable: a "command not found" error after installing, no guidance on storing a key safely, the API Keys page leading with identity federation (which suggested I didn't need a key), and subscription and API usage billed in two different places.

## What I'd research next

- **Contextual inquiry with first-time builders.** Watch people set up and build their first tool in real time, thinking aloud, to understand their mental model at each point where they stall. Where they get stuck tells you more than where they succeed.
- **Rapid prototyping of a better first run.** Use Claude to prototype improved first-run experiences, such as guided key setup that checks the key as you paste it, and test them with users within days. Iterating quickly on the experience delivers value faster than waiting for a full build.

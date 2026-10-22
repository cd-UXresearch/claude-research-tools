# Claude research tools

Two small tools that use Claude (Anthropic's AI model) to help with UX research:
one turns interview transcripts into a findings report, and one answers questions
across all your past studies. In both, **Claude does the reading and writing,
and plain code checks the facts.** Every quote is verified word for word, every
count is computed rather than guessed, and every citation is checked.

## A question the research can't answer

I asked the research agent a question with a false premise built in. LabLoop has
no calendar sync feature:

> **Why did users love the new calendar sync feature?**

It didn't go along with it. Here is its summary, exactly as given:

> *"The research doesn't show users loving a new calendar sync feature. None of
> the findings mention one existing. What participants described is the
> opposite: LabLoop doesn't sync with their personal calendars, and several
> asked for it. The findings can tell you why people want calendar sync, but not
> how they reacted to a released feature."*

It labelled the question **Not covered by the research**. It then backed up what
the studies *do* say, with sources and verified quotes:

> "It doesn't talk to my calendar. I live in Outlook. I have to look at LabLoop
> and then separately look at my calendar to see if I can actually make the time
> I booked. It's two places for one thing." (P01, *Sample transcripts*, finding 4,
> supported by 4 of 6 participants)

It also gave the disagreeing view (a PI who said calendar sync *"doesn't matter
much to me personally"*). Finally, it suggested what a future study could
investigate if the feature has since launched. All 8 citations in that answer
were checked by code against the reports.

> 📸 **[Screenshot placeholder: the calendar sync answer in the conversation page]**
> Save an image as `docs/agent-calendar-sync.png` and replace this line with
> `![The agent's answer to a false-premise question](docs/agent-calendar-sync.png)`

## The two tools

**research-synth** (`research_synth.py`) reads a folder of interview transcripts
and writes a findings report. Each finding has a headline, how many participants
support it ("4 of 6 participants"), 1 to 3 supporting quotes, any contrasting
views, and a flag when only one person raised it. What participants said
(*evidence*) is kept apart from what it might mean (*interpretation*).

**ask-research** (`ask_research.py`) is a chat. You ask a question in plain
language, such as "what do we know about booking conflicts?", and an agent
searches across every study's report to answer it. It cites the study, finding,
and participant for everything it says. When the research doesn't cover
something, it says so and suggests it might be worth studying.

### Why these exist

These grew out of an AI-augmented research practice I developed as a UX
researcher at a large pharma company. There, I'm the only researcher, so I need
two things. First, help for non-researchers to produce rigorous, evidence-backed
reports. Second, a way for teams to ask "what do we already know?" instead of
repeating old studies.

I rebuilt both ideas independently, on my own time, as a first-time developer
working with Claude Code. **No company code or data was used, for
confidentiality reasons.** Everything here, including the LabLoop app, its
users, and all 17 interview participants, is entirely fictional.

## How verification works

AI models can misquote, invent quotes, or get numbers wrong, so these tools
never take the model's word for facts. Ordinary Python code checks them:

- **Quotes are checked word for word.** Every quote in a report is searched for
  in the transcript of the participant it's credited to. Only typography is
  ignored: curly vs. straight quote marks, dash styles, and spacing. A quote that
  is paraphrased, invented, credited to the wrong person, or actually spoken by
  the interviewer is flagged in red. *(`quote_check.py`)*
- **Counts are computed, not generated.** "4 of 6 participants" comes from the
  list of supporting participants and the number of transcripts. The model is
  told never to state numbers.
- **The agent only sees verified quotes.** When the agent starts, every report is
  re-checked against its transcripts, and any quote that fails is removed before
  the agent can see it. *(`research_library.py`)*
- **Every agent citation is checked.** The agent must hand in its answer in a
  fixed structure. Code then checks that each finding it cites exists, and that
  each quote matches a verified quote in that finding, from the right
  participant. Failures are flagged in red.
- **Nothing paid for is lost.** Claude's raw answer is saved before any checking
  or formatting runs, so a bug can never throw away a response. Any page can be
  rebuilt from the saved answer for free.

The tests include deliberately wrong answers (invented quotes, misattributed
quotes, findings that don't exist) to confirm each one is caught.

In this example data, all **89 of 89** report quotes and all **77 of 77** agent
citations passed.

## See the examples

These are the real outputs from my test run, included in this repository:

- **Findings report:** [`reports/findings_2026-10-21_135950.html`](reports/findings_2026-10-21_135950.html)
  (Sample transcripts: 10 findings, 6 participants). There are also reports for
  the [onboarding](reports/findings_2026-10-21_151218.html) and
  [mobile](reports/findings_2026-10-21_151339.html) studies.
- **Agent conversation:** [`answers/conversation_2026-10-21_151824.html`](answers/conversation_2026-10-21_151824.html)
  (5 questions across all three studies, including the calendar sync one).

GitHub shows `.html` files as code rather than as pages. To see them as designed,
download the file and open it in your browser. Each report also has a
plain-markdown version (`.md`) that GitHub displays directly.

> 📸 **[Screenshot placeholder: the top of a findings report]**
> Save an image as `docs/findings-report.png` and replace this line with
> `![A findings report](docs/findings-report.png)`

> 📸 **[Screenshot placeholder: a finding card with verified quotes]**
> Save an image as `docs/finding-card.png` and replace this line with
> `![A finding card with verified quotes](docs/finding-card.png)`

> 📸 **[Screenshot placeholder: the agent chat in the terminal, showing its tool calls]**
> Save an image as `docs/agent-terminal.png` and replace this line with
> `![The agent working in the terminal](docs/agent-terminal.png)`

## Run it yourself

You'll need Python 3.10 or newer (I used Python 3.15 on macOS) and an Anthropic API key from
[platform.claude.com](https://platform.claude.com/settings/keys).

**1. Set up.** Download this repository, open Terminal in its folder, and run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

This creates a private Python environment for the project and installs the two
libraries it needs: the Anthropic SDK and `python-dotenv`.

**2. Add your API key.** Copy the template and put your key in it:

```bash
cp .env.example .env
```

Open `.env` in a text editor and replace `your-api-key-here` with your key. The
`.gitignore` file keeps `.env` out of version control, so your key isn't shared.
Never paste your key into a chat or commit it.

**3. Make a findings report** from one of the sample studies:

```bash
python research_synth.py sample_transcripts
```

The report opens in your browser when it's done (a few minutes).

**4. Ask the research a question:**

```bash
python ask_research.py
```

Type a question and press Enter. As the agent works, you'll see each tool call
it makes. Type `quit` to finish. The conversation is saved as a page in
`answers/` and opens in your browser.

**Try it for free first.** The tests run both tools against a fake Claude on your
own computer, with no API key and no cost:

```bash
python -m unittest discover tests
```

To use your own transcripts, put one `.txt` file per participant in a folder.
The format is in any of the sample files. Then run `python research_synth.py
your_folder`. Remove names and identifying details first, because transcripts
are sent to Anthropic's API for processing.

## What it cost

My whole test cost **US$1.13** in API usage. That covered synthesizing the three
studies (17 fictional participants), the 5-question agent conversation, and the
runs along the way while building. Each findings report cost between US$0.13 and
US$0.19 (the exact token counts are in each report's `_details.json` file). Both
tools use Claude Opus 5.5 by default.

## How it was built

I built this as a first-time developer, working with Claude Code. My notes on the
experience are in **[DEVELOPER_EXPERIENCE.md](DEVELOPER_EXPERIENCE.md)**: what
went well, where I got stuck (mostly API key setup), when building on the API
beats using the chat app, and what I'd recommend to Anthropic's platform team.

## What's in this repository

| Path | What it is |
|---|---|
| `research_synth.py` | Tool 1: transcripts to findings report |
| `quote_check.py` | Reads transcripts and checks quotes (no AI) |
| `html_report.py` | Designs the HTML findings report |
| `ask_research.py` | Tool 2: the research chat and the agent's tool loop |
| `research_library.py` | Loads reports, runs the agent's tools, checks its citations (no AI) |
| `answer_html.py` | Designs the HTML conversation page |
| `sample_transcripts/` | Fictional study: general use of LabLoop (P01 to P06) |
| `onboarding_interviews/` | Fictional study: onboarding new lab members (O01 to O06) |
| `mobile_interviews/` | Fictional study: using LabLoop on mobile (M01 to M05) |
| `reports/` | The three findings reports (HTML, markdown, Claude's raw answer, and run details) |
| `answers/` | The example agent conversation (HTML and raw JSON) |
| `tests/` | Automated tests, including full runs against a fake Claude |
| `DEVELOPER_EXPERIENCE.md` | Notes on building this as a first-time developer |
| `requirements.txt` | The Python libraries needed, with exact versions |
| `.env.example` | Template for your API key file |
| `.gitignore` | Keeps `.env` and local files out of version control |

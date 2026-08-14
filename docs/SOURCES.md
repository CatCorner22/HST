# Sources

Research underpinning `gonzo/style/style_spec.md`. The style spec is a **derived
analytical description** of stylistic mechanics built from these sources. No
copyrighted passages are reproduced in this repository, and the engine is
prohibited from reproducing them at runtime.

## Why a derived spec rather than a corpus

Copyright protects a particular *expression*, not a style or a voice. Two writers
may work in the same idiom provided neither copies the other's actual words.

- U.S. Copyright Office, *What Does Copyright Protect?* — https://www.copyright.gov/help/faq/faq-protect.html
- Copyright Alliance, *What Can You Copyright?* — https://copyrightalliance.org/faqs/what-can-you-copyright/
- U.S. Copyright Office *Compendium* ch. 700, Literary Works — https://www.copyright.gov/comp3/redlines/chap700.pdf

This is also the better engineering choice. A rule system can be inspected,
tuned, unit-tested, and scored. A scraped corpus can do none of those things.

## Primary — the writer on his own craft

- **Hunter S. Thompson, "The Art of Journalism No. 1," *The Paris Review* 156 (Fall 2000)**, interviewed by Douglas Brinkley and Terry McDonell. https://www.theparisreview.org/interviews/619/the-art-of-journalism-no-1-hunter-s-thompson
  - Composed by ear; read work aloud while drafting to hear how sentences played.
- **The typing exercise.** As a *Time* copy boy in 1958 he typed out *The Great Gatsby* and *A Farewell to Arms* word for word, plus Faulkner stories, "just to get the feeling" of writing that way — "If you type out somebody's work, you learn a lot about it. Amazingly it's like music." https://www.openculture.com/2017/06/hunter-s-thompson-typed-out-the-great-gatsby-farewell-to-arms.html
  - Rhythm is learned as *music*, not as grammar. Drives the Rhythm Law.
- **The letters** — *The Proud Highway* (1955–1967), ed. Douglas Brinkley. Roughly 200 letters showing the voice off-duty: sharp, sardonic, merciless toward pretension, unapologetic. Evidence the register is native, not a performance reserved for print. https://www.penguinrandomhouse.com/books/178191/proud-highway-by-hunter-s-thompson-foreword-by-william-j-kenney-edited-by-douglas-brinkley/

## Origin of the form

- **"The Kentucky Derby Is Decadent and Depraved," *Scanlan's Monthly*, June 1970**, illustrated by Ralph Steadman. The first gonzo piece. Boston Globe editor Bill Cardoso's reaction — "this is pure Gonzo" — named the form. Structurally decisive: Thompson and Steadman never actually saw the race. The assignment is a pretext; the crowd is the story. https://niemanstoryboard.org/2013/11/12/whys-this-so-good-no-87-hunter-s-thompson-and-the-kentucky-derby/ · https://grantland.com/features/looking-back-hunter-s-thompson-classic-story-kentucky-derby/

## Scholarship and criticism

- **Jason Mosser, "What's Gonzo about Gonzo Journalism?"** *Literary Journalism Studies* — https://ialjs.org/wp-content/uploads/2012/06/085-090_WhatsGonzoMosser.pdf
  - Names the mechanics directly: verb-driven "running" syntax; digressions, metaphors, fragments, allusions, ellipses, abrupt transitions and gaps. Scene-by-scene construction done *from inside the narrator's own head* — imagination, memory, and paranoid hallucination replace other New Journalists' access to other minds.
- **Neal Reid, "The Outlaw Rhetoric of Hunter S. Thompson"** (M.A. thesis, Auburn) — https://etd.auburn.edu/bitstream/handle/10415/9167/Neal%20Reid's%20HST%20thesis.FINAL.pdf?sequence=2
  - Hyperbole escalated to the absurd, but aimed: the exaggeration redirects attention onto a real atrocity, abuse of power, or marginalizing policy. Adjective strings as a signature. Dashes carrying interjected afterthought without breaking sincerity.
- **William Reynolds, "On the Road to Gonzo: Hunter S. Thompson's Early Literary Development,"** *Literary Journalism Studies* — https://ialjs.org/wp-content/uploads/2012/06/051-084_RoadtoGonzoReynolds.pdf
- **David S. Wills, "Gonzo Studies"** (Substack) — vocabulary acquisition traced through letters and drafts: *atavistic* enters after Fitzgerald's *Tender Is the Night*; *savage* during a Coleridge obsession ("A savage place!" — *Kubla Khan*). Signature words are borrowed and then worn to a groove. https://huntersthompson.substack.com/
- **"Specificity: The Secret Brilliance of Hunter S. Thompson,"** *Beatdom* — https://www.beatdom.com/specificity-the-secret-brilliance-of-hunter-s-thompson/
  - The most load-bearing source for this engine. Precision of detail is what makes the unreal passages land; he competed with Fitzgerald at writing with no wasted words.
- **"Guide to the classics: *Fear and Loathing in Las Vegas*,"** *The Conversation* — https://theconversation.com/guide-to-the-classics-fear-and-loathing-in-las-vegas-68734
  - The narrator as unreliable — mocking, exaggerating, at times simply lying — and the constant tone-shifting that goes with it.
- **The "wave speech"** (*Fear and Loathing in Las Vegas*, end of ch. 8). Described as sedate, honest, relatively unadorned — a deliberate break from the manic grotesquerie around it, and reportedly the passage he was proudest of and chose when asked to read aloud. The single most-skipped feature in imitations; the direct basis for the Elegiac band and the mandatory register break. https://www.eastportlandblog.com/2012/07/23/the-wave-speech-fear-and-loathing-in-las-vegas/
- **Bestial imagery in political writing** — *Fear and Loathing on the Campaign Trail '72* renders Nixon as a drooling, red-eyed, hyena-headed beast crawling out a White House window. Invective as a rhetorical mode, not decoration. https://en.wikipedia.org/wiki/Fear_and_Loathing_on_the_Campaign_Trail_%2772 · https://slate.com/news-and-politics/2012/06/hunter-s-thompson-fear-and-loathing-on-the-campaign-trail-72-review-by-matt-taibbi.html

## What the research changed about the design

1. **Specificity is the engine, not the decoration.** Hyperbole without concrete anchoring reads as noise. Enforced by the Specificity Anchor rule and measured by `specificity_density`.
2. **Dynamics beat intensity.** The elegiac break is load-bearing. Constant maximum volume is the single clearest tell of an imitation. Enforced by the register-band system and measured by `register_transitions` / `has_elegiac`.
3. **Tics are a budget, not a style.** Signature words were acquired gradually and used sparingly. Imitations overuse them immediately. Enforced by the anti-pastiche budget and measured by `tic_rate`.
4. **The comedy carries an indictment.** The hyperbole has a target. Without one the voice is just noise in a loud hat. Scored by the LLM judge, which counting cannot reach.

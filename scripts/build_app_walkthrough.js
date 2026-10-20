/**
 * Builds PROMPT_ENGINE_WALKTHROUGH.docx — the plain-language walkthrough of the
 * whole engine, written for a non-technical reader.
 *
 * The Word file is GENERATED. Edit the content in this script and rebuild it;
 * do not hand-edit the .docx, or the next rebuild discards the edit.
 *
 *   node scripts/build_app_walkthrough.js
 *
 * Every cycle that ships a significant feature must add a section (or amend
 * one) here and append a row to the change log at the bottom. See
 * .agents/rules/sdlc_protocol.md, Step 8c.
 */
const fs = require("fs");
const path = require("path");
const {
    AlignmentType,
    BorderStyle,
    Footer,
    Document,
    HeadingLevel,
    LevelFormat,
    Packer,
    PageBreak,
    PageNumber,
    Paragraph,
    ShadingType,
    Table,
    TableCell,
    TableRow,
    TextRun,
    WidthType,
} = require("docx");

/** A4 portrait with 1-inch margins leaves this much room for a table. */
const CONTENT_WIDTH = 9000;
const INK = "1F2933";
const ACCENT = "1D4ED8";
const MUTED = "5B6670";
const BAND = "EEF2F7";

const VERSION = "1.0";
const UPDATED = "20 October 2026";

/* ------------------------------------------------------------------ helpers */

function h1(text) {
    return new Paragraph({
        heading: HeadingLevel.HEADING_1,
        spacing: { before: 360, after: 160 },
        children: [new TextRun({ text, bold: true, size: 32, color: ACCENT })],
    });
}

function h2(text) {
    return new Paragraph({
        heading: HeadingLevel.HEADING_2,
        spacing: { before: 280, after: 120 },
        children: [new TextRun({ text, bold: true, size: 26, color: INK })],
    });
}

function h3(text) {
    return new Paragraph({
        heading: HeadingLevel.HEADING_3,
        spacing: { before: 200, after: 100 },
        children: [new TextRun({ text, bold: true, size: 23, color: INK })],
    });
}

/** A paragraph. Pass a string, or an array of {text, bold?, italics?}. */
function p(content, opts = {}) {
    const runs = (typeof content === "string" ? [{ text: content }] : content).map(
        (r) =>
            new TextRun({
                text: r.text,
                bold: r.bold ?? false,
                italics: r.italics ?? false,
                size: opts.size ?? 22,
                color: opts.color ?? INK,
            }),
    );
    return new Paragraph({
        spacing: { after: opts.after ?? 140, line: 300 },
        alignment: opts.alignment,
        children: runs,
    });
}

function quiet(text) {
    return p(text, { color: MUTED, size: 20 });
}

function bullet(content, level = 0) {
    const runs = (typeof content === "string" ? [{ text: content }] : content).map(
        (r) =>
            new TextRun({
                text: r.text,
                bold: r.bold ?? false,
                italics: r.italics ?? false,
                size: 22,
                color: INK,
            }),
    );
    return new Paragraph({
        numbering: { reference: "bullets", level },
        spacing: { after: 80, line: 300 },
        children: runs,
    });
}

function step(content) {
    const runs = (typeof content === "string" ? [{ text: content }] : content).map(
        (r) =>
            new TextRun({
                text: r.text,
                bold: r.bold ?? false,
                italics: r.italics ?? false,
                size: 22,
                color: INK,
            }),
    );
    return new Paragraph({
        numbering: { reference: "steps", level: 0 },
        spacing: { after: 100, line: 300 },
        children: runs,
    });
}

function cell(text, { bold = false, width, shade, size = 20 } = {}) {
    return new TableCell({
        width: { size: width, type: WidthType.DXA },
        shading: shade ? { type: ShadingType.CLEAR, fill: shade, color: "auto" } : undefined,
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        children: [
            new Paragraph({
                spacing: { after: 0, line: 280 },
                children: [new TextRun({ text, bold, size, color: INK })],
            }),
        ],
    });
}

/**
 * A table with a shaded header row. `widths` must sum to CONTENT_WIDTH.
 * Pass `header` as null for a plain two-column fact list with no header band.
 */
function table(header, rows, widths) {
    const head = header
        ? [
              new TableRow({
                  tableHeader: true,
                  children: header.map((t, i) =>
                      cell(t, { bold: true, width: widths[i], shade: BAND }),
                  ),
              }),
          ]
        : [];
    return new Table({
        width: { size: CONTENT_WIDTH, type: WidthType.DXA },
        columnWidths: widths,
        rows: [
            ...head,
            ...rows.map(
                (r) =>
                    new TableRow({
                        children: r.map((t, i) => cell(t, { width: widths[i] })),
                    }),
            ),
        ],
    });
}

function spacer() {
    return new Paragraph({ spacing: { after: 200 }, children: [] });
}

function rule() {
    return new Paragraph({
        spacing: { before: 200, after: 200 },
        border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "D4DBE3", space: 4 } },
        children: [],
    });
}

function pageBreak() {
    return new Paragraph({ children: [new PageBreak()] });
}

/* ------------------------------------------------------------------- content */

const body = [];

/* --- Cover --- */
body.push(
    new Paragraph({ spacing: { before: 2400, after: 0 }, children: [] }),
    new Paragraph({
        spacing: { after: 100 },
        children: [new TextRun({ text: "RankUno Prompt Engine", bold: true, size: 56, color: INK })],
    }),
    new Paragraph({
        spacing: { after: 400 },
        children: [
            new TextRun({
                text: "How the whole thing works, in plain language",
                size: 32,
                color: ACCENT,
            }),
        ],
    }),
    p(
        "A walkthrough of the engine and the app for anyone who needs to understand what it does, " +
            "what it measures and what it will not claim — without reading any code.",
        { size: 24, color: MUTED },
    ),
    spacer(),
    table(
        null,
        [
            ["Version", VERSION],
            ["Last updated", UPDATED],
            ["Audience", "Non-technical: account managers, clients, new joiners, leadership"],
            ["Maintained by", "Every build session that ships a significant feature (see the last section)"],
        ],
        [2400, 6600],
    ),
    pageBreak(),
);

/* --- 1. Read this first --- */
body.push(
    h1("1. Read this first"),
    p(
        "This document is the single place where the engine is explained for people who do not read code. " +
            "It is written so that someone meeting the product for the first time can follow it end to end and " +
            "come away able to explain it to a client.",
    ),
    p([
        { text: "Three promises about this document. ", bold: true },
        {
            text: "First, it describes only what the engine actually does today — not what is planned. " +
                "Second, where the engine is weaker than it sounds, this document says so out loud; " +
                "section 11 exists for exactly that. Third, it is kept current: every time a significant " +
                "feature is built, the session that built it updates this file and adds a line to the change log.",
        },
    ]),
    quiet(
        "The Word file is generated from scripts/build_app_walkthrough.js. If you need to change the wording, " +
            "change it there and rebuild — a hand-edit of the .docx is lost on the next rebuild.",
    ),
    rule(),
);

/* --- 2. The problem --- */
body.push(
    h1("2. The problem this exists to solve"),
    p(
        "For twenty years, the question a brand asked was “where do we rank on Google?” Someone typed " +
            "a few words, got ten blue links, and the brand's job was to be one of them.",
    ),
    p(
        "That is no longer how a large and growing share of buyers behave. They ask a question in full " +
            "sentences — of ChatGPT, of Perplexity, of Gemini, or of Google itself, which now answers above " +
            "the links — and they get one composed answer with a handful of sources. There is no page two. " +
            "Either the answer names you and links to you, or you were not in the conversation at all.",
    ),
    p([
        { text: "That creates a measurement gap. ", bold: true },
        {
            text: "A brand can hold position three on Google and still be invisible in the AI answer sitting " +
                "above it. Nothing in a traditional rank tracker can see that, because the thing being " +
                "measured is no longer a ranked list — it is a paragraph of prose that is different every " +
                "time you ask.",
        },
    ]),
    p(
        "The Prompt Engine closes that gap. It asks the real AI answer engines real buying questions, " +
            "on a schedule, and records what they said and who they credited. Everything else in this " +
            "document is a consequence of that one activity.",
    ),
    rule(),
);

/* --- 3. What it does --- */
body.push(
    h1("3. What the engine does, in four sentences"),
    bullet([
        { text: "It asks. ", bold: true },
        {
            text: "A project holds a list of real buying questions (“prompts”), and the engine puts " +
                "each one to each AI engine, worded exactly as written, several times.",
        },
    ]),
    bullet([
        { text: "It reads the answer. ", bold: true },
        {
            text: "For every answer it records whether your brand was named, whether your website was " +
                "credited as a source, which competitors were, and in what order.",
        },
    ]),
    bullet([
        { text: "It keeps the history. ", bold: true },
        {
            text: "Nothing is thrown away, so the same question asked sixty times over two months becomes a " +
                "trend rather than an anecdote.",
        },
    ]),
    bullet([
        { text: "It tells you what to do. ", bold: true },
        {
            text: "From the stored answers it produces a ranked list of specific, evidenced actions — this " +
                "page, this claim, this competitor — and a client-ready PDF.",
        },
    ]),
    spacer(),
    p([
        { text: "The one thing to hold on to: ", bold: true },
        {
            text: "the engine never asks a leading question. Your brand name is never inserted into the " +
                "prompt. The question goes out exactly as a buyer would ask it, and the brand is only used " +
                "afterwards, to read the answer. If the engine reports that you were cited, nothing in the " +
                "question prompted that.",
        },
    ]),
    rule(),
);

/* --- 4. Glossary --- */
body.push(
    h1("4. The words you will see on screen"),
    p(
        "These eleven terms carry nearly all the meaning in the app. Everything else can be worked out " +
            "from context.",
    ),
    spacer(),
    table(
        ["Term", "What it means"],
        [
            [
                "Prompt",
                "One real buying question, in full sentences, as a buyer would type it. Example: “What is the best procurement software for enterprise manufacturers?”",
            ],
            [
                "Platform / engine",
                "One AI answer engine the question is put to: Google AI Overview, ChatGPT Search, Perplexity or Gemini.",
            ],
            [
                "Sample",
                "One answer to one question from one engine. Because these engines answer differently each time, the engine takes several samples and reports the pattern, not a single reply.",
            ],
            [
                "Crawl (or run)",
                "One pass over the project: every question due, on every platform due, the agreed number of samples each.",
            ],
            [
                "Cited",
                "Your website was credited as a source of the answer — the link a reader can click. This is the hard currency of AI visibility.",
            ],
            [
                "Mentioned",
                "Your brand name appeared in the answer text, but without a link. Being mentioned and not cited is a very common and very fixable position.",
            ],
            [
                "Citation rate",
                "Of the answers that worked, the share that cited you. “2 of 3” is a 67% citation rate.",
            ],
            [
                "Confidence band",
                "The honest margin of error on a rate. “Cited 67%, likely 35–96%” means nine samples cannot tell the difference between a good month and a lucky one. Narrow bands need more samples.",
            ],
            [
                "Consolidation",
                "The engine pools several crawls together and publishes one dated position set from them. This is the number you report to a client; a single crawl is too noisy to report.",
            ],
            [
                "Action card",
                "One specific recommended fix, with the evidence behind it and the exact URL it concerns. Scored improved, unchanged or regressed after the next consolidation.",
            ],
            [
                "Content gap",
                "The engine found a question you should be winning and no page of yours that answers it.",
            ],
        ],
        [1900, 7100],
    ),
    rule(),
);

/* --- 5. How one measurement happens --- */
body.push(
    h1("5. How one measurement actually happens"),
    p(
        "This is the sequence behind every number in the app. It is worth reading once, because it " +
            "explains why the engine is slower and more careful than a rank tracker.",
    ),
    spacer(),
    step([
        { text: "A question is chosen. ", bold: true },
        {
            text: "Either you supplied it, or the engine built it: it buys real search demand data " +
                "(Semrush), takes the questions buyers genuinely ask, and generates a balanced set of twenty " +
                "— ten that name a brand, ten that do not.",
        },
    ]),
    step([
        { text: "The question is filtered. ", bold: true },
        {
            text: "A question with no commercial intent is dropped before anyone pays for it. Questions you " +
                "supplied yourself always pass; the filter only advises on those.",
        },
    ]),
    step([
        { text: "The money is reserved. ", bold: true },
        {
            text: "Before any call is made, its cost is charged against this session's ceiling and today's " +
                "ceiling. If either is reached, the crawl stops cleanly and everything already collected is " +
                "kept.",
        },
    ]),
    step([
        { text: "The engines are asked. ", bold: true },
        {
            text: "Each engine is asked with its own web search deliberately switched on, so the answer is " +
                "grounded in live pages rather than in the model's memory. Several samples are taken. If the " +
                "first two agree, the engine can stop there and save the rest of the budget.",
        },
    ]),
    step([
        { text: "The answer is read. ", bold: true },
        {
            text: "Every cited link is recorded with its position; your own brand and aliases are matched " +
                "whole-word in the answer text, with the sentence they appear in; competitors are matched the " +
                "same way. Google's own ranked results are captured from the same call, free, so AI " +
                "visibility and classic rank sit side by side.",
        },
    ]),
    step([
        { text: "Everything is stored. ", bold: true },
        {
            text: "The full answer text, the engine's own internal search queries, which sentence each source " +
                "supports, the pages the engine read but chose not to cite, and the model version that " +
                "answered. All of it, every time.",
        },
    ]),
    step([
        { text: "The findings are computed. ", bold: true },
        {
            text: "Nothing is bought at this stage. The verdicts, trends, action cards, claims and reports " +
                "are all derived from what is already stored, which is why browsing the app costs nothing.",
        },
    ]),
    spacer(),
    p([
        { text: "Why several samples. ", bold: true },
        {
            text: "Ask ChatGPT the same question three times and you can get three different source lists. " +
                "A single answer is an anecdote. The engine's whole design — samples, then crawls, then " +
                "consolidations, each with a confidence band — exists to turn that noise into something you " +
                "can defend in a client meeting.",
        },
    ]),
    rule(),
);

/* --- 6. The engines --- */
body.push(
    h1("6. The four engines, and what is honest about each"),
    p(
        "The engine calls each vendor's real API with web search forced on. It does not scrape the " +
            "consumer chat window, and this document will not pretend the two are identical.",
    ),
    spacer(),
    table(
        ["Engine", "What is actually asked", "Worth knowing"],
        [
            [
                "Google AI Overview",
                "A real Google search, plus the AI Overview panel it returns.",
                "Also returns the classic ranked results in the same call, at no extra cost. This is how you see “we rank 3rd but we are absent from the AI answer”.",
            ],
            [
                "ChatGPT Search",
                "OpenAI's API with the web-search tool forced on.",
                "Uniquely reports the pages it read and chose not to cite — the “read but rejected” signal, which is the most actionable thing in the product.",
            ],
            [
                "Perplexity",
                "Perplexity's API with search forced on.",
                "Returns the source passages and their dates, so freshness problems are visible. Also reports its own cost, so spend on it is exact rather than estimated.",
            ],
            [
                "Gemini",
                "Google's Gemini API with search grounding on.",
                "Its API has no location field at all, so a Gemini answer reflects the billing account's country, not the project's market. The app labels this rather than hiding it.",
            ],
        ],
        [1700, 3000, 4300],
    ),
    spacer(),
    p([
        { text: "The fair-use point to make to a client: ", bold: true },
        {
            text: "an API answer with search forced on is the closest honest proxy for what a buyer sees in " +
                "the chat window. It is not a screenshot of their session. Anyone who claims otherwise is " +
                "overselling.",
        },
    ]),
    pageBreak(),
);

/* --- 7. The app, screen by screen --- */
body.push(
    h1("7. Walking through the app"),
    p(
        "The app opens on a list of projects. Down the left are five destinations; inside a project are " +
            "seven tabs. That is the whole surface.",
    ),
    spacer(),
    table(
        ["Left sidebar", "What it is for"],
        [
            ["Projects", "Every client you track. The home screen."],
            ["Dashboard", "Jumps straight back into the project you last looked at."],
            ["Atlas", "The research library: every question, answer and gap for a line of business."],
            ["Trends", "One chart, one question, over time."],
            ["Costs", "What has been spent, by vendor and by project."],
        ],
        [2200, 6800],
    ),
    spacer(),

    h2("7.1  Projects"),
    p(
        "A project is one client in one market. It holds the brand name and its aliases, the websites you " +
            "own, the competitors to watch, the line of business, the seed keywords, your landing pages, " +
            "which platforms to track, and how often to crawl.",
    ),
    p([
        { text: "One market per project. ", bold: true },
        {
            text: "A project's market — country, language, city — is frozen once it has crawled, because " +
                "changing it mid-history would silently make the trend line compare two different things. " +
                "Tracking the same brand in a second country means a second project. That is a deliberate " +
                "constraint, not a missing feature.",
        },
    ]),

    h3("Who is allowed to change a project"),
    p(
        "Everyone who can open the app can read every project. Changing one is different: when a project " +
            "is created you set an owner name and password for it, and after that only someone holding that " +
            "credential can edit it, run it, tick its action cards or delete it.",
    ),
    bullet("Readers see “Read-only · owner X” in the project header and an Unlock to edit button."),
    bullet("The first refused change opens the unlock dialog, then carries on where it left off."),
    bullet("The credential is held for that browser tab only, never saved to the project."),
    bullet(
        "There is no password reset. A recovery password can be configured for the whole installation if you want one.",
    ),

    h2("7.2  Overview"),
    p(
        "The health screen. For each platform it answers: are we cited, how often, is that moving, and how " +
            "sure are we?",
    ),
    bullet([
        { text: "Headline rates with their bands. ", bold: true },
        { text: "“Cited 67%, likely 35–88%” — never a bare percentage pretending to precision it does not have." },
    ]),
    bullet([
        { text: "What changed. ", bold: true },
        { text: "Movement since the previous consolidation, not since the last crawl, so one noisy crawl cannot create a story." },
    ]),
    bullet([
        { text: "How the engines describe you. ", bold: true },
        {
            text: "Every sentence that names you or a competitor is scored for tone and for the attributes " +
                "attached to it (“expensive”, “enterprise-grade”, “hard to implement”). " +
                "The screen shows the negative share per platform and the single worst sentence, with the " +
                "source behind it.",
        },
    ]),
    bullet([
        { text: "Where your mentions sit. ", bold: true },
        {
            text: "Being one of eleven names in a list is not the same as being the subject of a paragraph. " +
                "The engine records which it was, how early in the answer, and which site the engine got it from.",
        },
    ]),

    h2("7.3  Battleground"),
    p(
        "The grid. Questions down the side, platforms across the top, one cell per pair, grouped by " +
            "subtopic. It is the fastest way to see shape: a column that is empty is a platform you do not " +
            "exist on; a row that is empty is a question you are losing everywhere.",
    ),
    p(
        "Click any cell and a drawer opens with the evidence: the answers themselves, every cited link, " +
            "who else was credited and where you sat in Google's ranked results for the same question. The " +
            "grid can be read as the consolidated view (the reportable number) or as a single chosen crawl " +
            "(the raw moment).",
    ),

    h2("7.4  Prompts"),
    p(
        "The question list. Paste them in bulk, one per line, or add them one at a time. Text is sent to " +
            "the engines exactly as written — the app will not tidy, expand or rephrase it.",
    ),
    bullet("Star the questions that matter most to the client; starred questions are never sampled less."),
    bullet(
        "Give any single question its own schedule, its own platforms or its own sample count, or reset it to inherit the project's.",
    ),
    bullet(
        "Rewording a question keeps its history. The engine deliberately does not re-key a question when its text or the project's line of business changes, so a year of trend survives an edit.",
    ),

    h2("7.5  Runs & spend"),
    p(
        "Where crawls are started and watched. You can run everything due, everything regardless, or just " +
            "the questions you selected.",
    ),
    p(
        "A crawl runs in the background with a live progress bar — checks completed, paid calls made, " +
            "elapsed time, current phase. Closing the page does not stop it, and reopening re-attaches to it. " +
            "When it finishes you get a toast, a browser notification if the tab is in the background, and a " +
            "flashing tab title.",
    ),
    p([
        { text: "The same tab shows what the crawl cost: ", bold: true },
        {
            text: "every vendor call is recorded with its vendor, operation, model, latency and price — the " +
                "vendor's own reported figure where it gives one, a list price otherwise.",
        },
    ]),

    h2("7.6  Actions"),
    p(
        "The answer to “so what do we actually do?” The engine reads everything it has stored and " +
            "produces a ranked list of specific fixes. Each card carries the evidence, the exact URLs, the " +
            "questions it applies to, and a prescription. An analyst can set a status, an owner and a note, " +
            "and after the next consolidation the card is scored improved, unchanged or regressed.",
    ),
    spacer(),
    table(
        ["Card", "What it is telling you"],
        [
            ["Convert mention to citation", "The engines name you but do not link you. Usually the cheapest win available."],
            ["Own the claim a competitor owns", "A specific factual claim in the answer is credited to a competitor's page. Yours should own it."],
            ["Read but rejected", "An engine fetched your page and chose not to cite it. The page is findable but not usable as an answer."],
            ["AI Overview gap", "You rank in Google's classic results but are absent from the AI Overview above them."],
            ["Earned placement", "A third-party page (a review site, a listicle) is carrying you into the answer. Worth defending."],
            ["Freshness", "The sources being cited are newer than your pages."],
            ["Defend", "You hold this citation today. Keep the page current so you keep it."],
            ["Landing page missing", "A real question with no page of yours behind it — a content gap."],
            ["Correct a negative claim", "An engine is saying something unflattering. The card carries the quote and the source it came from."],
            ["Fetched but never cited", "Your access log shows a crawler fetching a page repeatedly, and its engine has never cited it."],
        ],
        [2600, 6400],
    ),

    h2("7.7  Crawler logs"),
    p(
        "Everything above measures what the engines said. This tab measures what they did. Upload your " +
            "web server's access log and the engine identifies the AI crawlers in it — OAI-SearchBot, " +
            "GPTBot, PerplexityBot, ClaudeBot, Googlebot, Applebot and the rest — and joins what they " +
            "fetched to what their engines went on to cite.",
    ),
    p(
        "That join produces the funnel that explains a bad result: fetched and cited, fetched and never " +
            "cited, or fetched and blocked. A page fetched two hundred times and cited zero times is a very " +
            "different problem from a page that was never discovered, and until you have the log you cannot " +
            "tell them apart.",
    ),
    p([
        { text: "What this tab does with the file matters, and you can say it to a client plainly. ", bold: true },
        {
            text: "The log is filtered in your own browser before anything is sent, so only lines naming a " +
                "known crawler leave the machine. What is kept is per-day counts per crawler and per page — " +
                "no IP addresses, no request lines, no referrers, no query strings, no user agents. Paths that " +
                "look like tokens or email addresses are dropped. An address is used once, in memory, to check " +
                "the crawler against the vendor's published address ranges, and what survives is a count of " +
                "verified fetches. Old rows are purged on a schedule. The app never displays a line of your " +
                "file back to you, by design.",
        },
    ]),

    h2("7.8  Reports & alerts"),
    p(
        "The client-facing output. One button produces a white-label PDF: cover, headline rates with their " +
            "confidence intervals, a platform scorecard, what changed, how the engines describe the brand, " +
            "the recommended actions and the exact URLs behind them.",
    ),
    p([
        { text: "The executive summary is written by a language model and then checked against the numbers. ", bold: true },
        {
            text: "The model is shown the computed figures and nothing else, and any sentence containing a " +
                "figure the engine did not actually measure is thrown away and replaced with the plain " +
                "deterministic wording. With no model key configured the whole narrative is deterministic. " +
                "The report cannot invent a statistic, which is the only basis on which it is safe to send one " +
                "to a client.",
        },
    ]),
    p(
        "Alerts are the other half. When a crawl closes a consolidation window it can send a Slack message " +
            "or an email. By default only one thing qualifies: a citation-rate drop whose confidence " +
            "intervals no longer overlap — in other words, a drop that the samples can actually support. " +
            "There is a cooldown, a daily ceiling, and a setting that switches all sending off.",
    ),

    h2("7.9  Atlas, Trends and Costs"),
    bullet([
        { text: "Atlas ", bold: true },
        {
            text: "is the research library for a line of business: every question, every answer, the citation " +
                "and mention rate per engine, the gaps where nobody owns the topic, and the snippets behind each. " +
                "Useful in a pitch, because it describes the category rather than one client.",
        },
    ]),
    bullet([
        { text: "Trends ", bold: true },
        { text: "charts one question's history on one platform, so you can see a move rather than infer it." },
    ]),
    bullet([
        { text: "Costs ", bold: true },
        {
            text: "shows spend by vendor, by crawl and by source, and proposes updated per-call price " +
                "estimates from what has actually been charged, so the budget ceilings stay honest as vendor " +
                "prices move.",
        },
    ]),
    rule(),
);

/* --- 8. Money --- */
body.push(
    h1("8. How spending is controlled"),
    p(
        "Every question put to an engine costs real money, so this is the part of the design with the " +
            "most belt and braces. Four mechanisms, each independent of the others.",
    ),
    spacer(),
    table(
        ["Control", "What it does"],
        [
            [
                "Approval before anything runs",
                "The crawl refuses to start unless an operator has explicitly approved it or an unattended budget has been set for scheduled runs. The default is to refuse.",
            ],
            [
                "Two ceilings",
                "One on what this running process may reserve, one on what has actually been spent since midnight UTC. The daily figure is read back from the ledger, so restarting the server does not reset the budget.",
            ],
            [
                "A hard call cap",
                "A maximum number of engine calls per crawl, independent of the money, as a second kind of stop.",
            ],
            [
                "Charged before it is spent",
                "Each call is reserved against the ceilings before it is made, not after. Hitting a ceiling ends the crawl cleanly and keeps everything already collected.",
            ],
        ],
        [2600, 6400],
    ),
    spacer(),
    h3("And four ways it avoids spending in the first place"),
    bullet("If the first two samples agree on whether you were cited, the rest are not bought."),
    bullet("A question measured very recently is reused rather than re-bought, so a crashed crawl re-runs almost free."),
    bullet("A vendor that keeps failing is skipped at no cost until a cooldown passes, instead of being retried on every question."),
    bullet(
        "A project can be set to sample stable question-platform pairs less often and spend the saving on the volatile ones. Net spend never exceeds the fixed policy; starred and manually overridden questions are never touched.",
    ),
    rule(),
);

/* --- 9. Scheduling --- */
body.push(
    h1("9. How often it runs, and what “stable” means"),
    p(
        "A project has a default interval — daily, every two or three days, weekly, fortnightly, monthly, " +
            "or any custom value. A question is due on a platform when its last sample there is older than " +
            "its interval, so the schedule is derived from history rather than from a calendar that can drift " +
            "out of step with reality.",
    ),
    p(
        "A second, separate setting says how many crawls to pool before publishing a position. The default " +
            "is three. With a two-day interval that means a fresh reportable position every six days, built " +
            "from three crawls of samples. Every crawl still keeps its own dated snapshots; consolidation is " +
            "an additional, steadier layer on top.",
    ),
    p([
        { text: "Stability is the engine's own verdict on whether a number can be trusted yet. ", bold: true },
        {
            text: "Over the most recent crawls it pools the samples and reads the confidence band. The " +
                "sentence it shows is the whole rule, and it is deliberately unglamorous: “Across the last " +
                "4 crawls, 0 of 12 answers cited you (likely 0%–24%): stable.” A pair is volatile when " +
                "the band straddles the halfway mark or the verdict keeps flipping, and unknown when there is " +
                "simply not enough data yet. The app would rather say “unknown” than guess.",
        },
    ]),
    rule(),
);

/* --- 10. Demo --- */
body.push(
    h1("10. The demonstration project"),
    p(
        "There is a seeded project called “GEP demo — full capability”: twenty questions, thirty " +
            "crawls at a two-day interval across two months, consolidations every three crawls, full samples, " +
            "classic ranks, cost rows, action card states, sentiment, crawler logs and the funnel.",
    ),
    p([
        { text: "Every figure in it is invented. ", bold: true },
        {
            text: "No vendor is ever called to produce it, and the project's own notes say it is a " +
                "demonstration. It exists so the whole product can be shown — including the parts that need " +
                "two months of history to mean anything — without spending a rupee or waiting two months. " +
                "Use it for demos and training; never quote a number from it to a client.",
        },
    ]),
    rule(),
);

/* --- 11. Limits --- */
body.push(
    h1("11. What the engine does not do"),
    p(
        "This section is the reason the rest of the document can be trusted. These are real limits, stated " +
            "plainly, so nobody oversells the product in a room where it matters.",
    ),
    spacer(),
    table(
        ["Limit", "What it means in practice"],
        [
            [
                "It is not the consumer chat window",
                "The engine calls each vendor's API with search forced on. That is the closest honest proxy for what a buyer sees, not a recording of their session.",
            ],
            [
                "Gemini ignores the market",
                "Gemini's API has no location field, so a Gemini answer follows the billing account's country rather than the project's market. The app labels this; it cannot fix it.",
            ],
            [
                "One market per project",
                "A project's market is frozen after its first crawl. A second country is a second project.",
            ],
            [
                "A number needs samples before it means anything",
                "Nine samples produce a confidence band wide enough to swallow most of the stories you might want to tell. The band is shown precisely so that nobody reports the midpoint as fact.",
            ],
            [
                "“Cited” is a majority verdict",
                "A question cited in one of three answers is recorded as not cited, while still keeping the real rank it achieved. The underlying counts are available, but the headline is a majority.",
            ],
            [
                "The crawler funnel needs your log",
                "Without an uploaded access log the engine can see what was cited but not what was fetched, so it cannot tell “never discovered” from “read and rejected”.",
            ],
            [
                "Sentiment scoring needs a model key",
                "With no language-model key configured, tone and attribute scoring simply does not run, and the report narrative falls back to deterministic wording.",
            ],
            [
                "It will not rephrase your questions",
                "Prompts go to the engines exactly as written. A badly worded question produces a faithfully measured bad answer.",
            ],
        ],
        [2800, 6200],
    ),
    rule(),
);

/* --- 12. Maintenance --- */
body.push(
    h1("12. Keeping this document current"),
    p(
        "This document is only worth having if it is true. The rule is therefore part of the build " +
            "process, not a good intention:",
    ),
    spacer(),
    p([
        {
            text: "Any cycle that ships a feature a client or an account manager would notice must update " +
                "this walkthrough in the same change as the code, and append a row to the change log below.",
            bold: true,
        },
    ]),
    spacer(),
    p("In practice that means:"),
    bullet("A new tab or screen gets a new sub-section in section 7."),
    bullet("A new kind of recommendation gets a row in the Actions table."),
    bullet("A new engine, vendor or data source gets a row in section 6 or section 8."),
    bullet(
        "A newly discovered limitation gets a row in section 11 — that section is the most valuable part of this document and the easiest to let rot.",
    ),
    bullet("A change that alters nothing a non-technical reader would notice needs no update here."),
    spacer(),
    quiet(
        "Mechanically: edit scripts/build_app_walkthrough.js, run node scripts/build_app_walkthrough.js, " +
            "and commit the regenerated PROMPT_ENGINE_WALKTHROUGH.docx alongside it. The rule is registered " +
            "as Step 8c in .agents/rules/sdlc_protocol.md so it is read at the end of every cycle.",
    ),
    spacer(),
    h2("Change log"),
    table(
        ["Version", "Date", "What changed", "Cycle"],
        [
            [
                "1.0",
                "20 Oct 2026",
                "First edition. Covers the engine, the four answer engines, all seven project tabs, spend control, scheduling and stability, the demo project and the known limits.",
                "—",
            ],
        ],
        [1100, 1500, 5100, 1300],
    ),
);

/* -------------------------------------------------------------------- build */

const doc = new Document({
    creator: "RankUno",
    title: "RankUno Prompt Engine — Walkthrough",
    description: "Plain-language walkthrough of the Prompt Engine for non-technical readers.",
    numbering: {
        config: [
            {
                reference: "bullets",
                levels: [
                    {
                        level: 0,
                        format: LevelFormat.BULLET,
                        text: "•",
                        alignment: AlignmentType.LEFT,
                        style: { paragraph: { indent: { left: 420, hanging: 260 } } },
                    },
                    {
                        level: 1,
                        format: LevelFormat.BULLET,
                        text: "◦",
                        alignment: AlignmentType.LEFT,
                        style: { paragraph: { indent: { left: 840, hanging: 260 } } },
                    },
                ],
            },
            {
                reference: "steps",
                levels: [
                    {
                        level: 0,
                        format: LevelFormat.DECIMAL,
                        text: "%1.",
                        alignment: AlignmentType.LEFT,
                        style: { paragraph: { indent: { left: 460, hanging: 300 } } },
                    },
                ],
            },
        ],
    },
    sections: [
        {
            properties: { page: { margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } } },
            footers: {
                default: new Footer({
                    children: [
                        new Paragraph({
                            alignment: AlignmentType.RIGHT,
                            children: [
                                new TextRun({
                                    text: "RankUno Prompt Engine · walkthrough v" + VERSION + " · ",
                                    size: 16,
                                    color: MUTED,
                                }),
                                new TextRun({ children: [PageNumber.CURRENT], size: 16, color: MUTED }),
                            ],
                        }),
                    ],
                }),
            },
            children: body,
        },
    ],
});

const out = path.join(__dirname, "..", "PROMPT_ENGINE_WALKTHROUGH.docx");
Packer.toBuffer(doc).then((buf) => {
    fs.writeFileSync(out, buf);
    console.log(`wrote ${out} (${buf.length} bytes)`);
});

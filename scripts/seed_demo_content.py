"""Content for the demonstration project seeded by `seed_demo_project.py`.

Everything here is invented for a demo and clearly labelled as such in the
project notes: prompts, answer sentences, page URLs, fan-out queries. It
exists so every capability the engine actually has can be shown on screen.
"""

from __future__ import annotations

from typing import Final

BRAND: Final = "GEP"
LOB: Final = "GEP Procurement (demo)"
PROJECT_NAME: Final = "GEP demo - full capability"
CLIENT_DOMAINS: Final = ["gep.com"]
COMPETITORS: Final = ["coupa.com", "sap.com", "oracle.com", "jaggaer.com", "ivalua.com"]
COMPETITOR_NAMES: Final = ["Coupa", "SAP Ariba", "Oracle", "Jaggaer", "Ivalua"]
ALIASES: Final = ["GEP SMART", "GEP NEXXE"]
SEED_KEYWORDS: Final = ["procurement software", "source to pay software", "spend analysis software"]

CLIENT_PAGES: Final = {
    "Source-to-Pay": "https://www.gep.com/software/gep-smart/source-to-pay",
    "Procure-to-Pay": "https://www.gep.com/software/gep-smart/procure-to-pay",
    "Spend Analysis": "https://www.gep.com/software/gep-smart/spend-analysis",
    "Supplier Management": "https://www.gep.com/software/gep-smart/supplier-management",
    "Reviews & comparisons": "https://www.gep.com/software/gep-smart/why-gep",
    "Implementation & pricing": "https://www.gep.com/software/gep-smart/pricing",
}
REJECTED_CLIENT_PAGE: Final = "https://www.gep.com/blog/ai-in-procurement-guide"

Prompt = tuple[str, str, str, int, str, str, str, str | None]
# text, subtopic, keyword, volume, intent, stage, type, mapped page key (None = content gap)
S2P, P2P, SPEND, SUPPLIER = (
    "Source-to-Pay",
    "Procure-to-Pay",
    "Spend Analysis",
    "Supplier Management",
)
REVIEWS, AI, IMPL = "Reviews & comparisons", "Procurement AI", "Implementation & pricing"
INFO, COMM, TRANS = "INFORMATIONAL", "COMMERCIAL", "TRANSACTIONAL"
AWARE, CONSIDER, DECIDE, POST = "AWARENESS", "CONSIDERATION", "DECISION", "POST_PURCHASE"
B, NB = "BRANDED", "NON_BRANDED"
PROMPTS: Final[list[Prompt]] = [
    (
        "What is source-to-pay software and how does it differ from procure-to-pay?",
        S2P,
        "source to pay software",
        2400,
        INFO,
        AWARE,
        NB,
        S2P,
    ),
    (
        "What are the key features of GEP SMART for source-to-pay?",
        S2P,
        "gep smart features",
        540,
        COMM,
        CONSIDER,
        B,
        S2P,
    ),
    (
        "Is GEP SMART a good source-to-pay suite for a global manufacturer?",
        S2P,
        "gep smart review",
        390,
        COMM,
        DECIDE,
        B,
        S2P,
    ),
    (
        "How does AI-driven spend analysis classify procurement data?",
        SPEND,
        "spend analysis software",
        1300,
        INFO,
        AWARE,
        NB,
        SPEND,
    ),
    (
        "Which spend analysis tools work best for a mid-sized manufacturer?",
        SPEND,
        "spend analysis tools",
        880,
        COMM,
        CONSIDER,
        NB,
        SPEND,
    ),
    (
        "GEP SMART vs Coupa: which is better for spend analysis?",
        SPEND,
        "gep smart vs coupa",
        430,
        COMM,
        CONSIDER,
        B,
        SPEND,
    ),
    (
        "What is the best procure-to-pay software for mid-market companies?",
        P2P,
        "procure to pay software",
        1900,
        COMM,
        CONSIDER,
        NB,
        P2P,
    ),
    (
        "How do you automate invoice matching in a procure-to-pay process?",
        P2P,
        "invoice matching automation",
        720,
        INFO,
        AWARE,
        NB,
        P2P,
    ),
    (
        "Does GEP SMART handle procure-to-pay end to end?",
        P2P,
        "gep smart procure to pay",
        260,
        COMM,
        DECIDE,
        B,
        P2P,
    ),
    (
        "What are leading approaches to digital supplier risk management?",
        SUPPLIER,
        "supplier risk management software",
        1600,
        INFO,
        AWARE,
        NB,
        SUPPLIER,
    ),
    (
        "Which supplier management platforms include risk scoring and onboarding?",
        SUPPLIER,
        "supplier management platform",
        990,
        COMM,
        CONSIDER,
        NB,
        SUPPLIER,
    ),
    (
        "Can GEP SMART score supplier risk automatically?",
        SUPPLIER,
        "gep supplier risk",
        210,
        COMM,
        DECIDE,
        B,
        SUPPLIER,
    ),
    (
        "What do customers say about GEP SMART in reviews?",
        REVIEWS,
        "gep smart reviews",
        590,
        COMM,
        DECIDE,
        B,
        REVIEWS,
    ),
    (
        "Coupa vs Ivalua vs GEP SMART: which source-to-pay suite should an enterprise choose?",
        REVIEWS,
        "source to pay comparison",
        640,
        COMM,
        DECIDE,
        NB,
        REVIEWS,
    ),
    (
        "What are the top-rated procurement software vendors this year?",
        REVIEWS,
        "top procurement software",
        3100,
        COMM,
        CONSIDER,
        NB,
        REVIEWS,
    ),
    (
        "How is generative AI used in procurement software today?",
        AI,
        "ai in procurement",
        2100,
        INFO,
        AWARE,
        NB,
        None,
    ),
    (
        "Which procurement AI agents can draft sourcing events automatically?",
        AI,
        "procurement ai agents",
        480,
        COMM,
        CONSIDER,
        NB,
        None,
    ),
    (
        "How does GEP use AI across GEP SMART and GEP NEXXE?",
        AI,
        "gep ai",
        330,
        INFO,
        CONSIDER,
        B,
        S2P,
    ),
    (
        "How long does a GEP SMART implementation take and what does it cost?",
        IMPL,
        "gep smart pricing",
        880,
        TRANS,
        DECIDE,
        B,
        IMPL,
    ),
    (
        "How do I integrate GEP SMART with SAP S/4HANA and Oracle ERP?",
        IMPL,
        "gep smart integration",
        350,
        INFO,
        POST,
        B,
        IMPL,
    ),
]

OPENERS: Final[dict[str, list[str]]] = {
    S2P: [
        "Source-to-pay (S2P) covers the full cycle from sourcing and contracting through "
        "purchasing and invoice settlement.",
        "A source-to-pay suite unifies strategic sourcing, contract management and "
        "procure-to-pay on one data model.",
    ],
    SPEND: [
        "Spend analysis aggregates invoice and purchase-order data, cleanses it and "
        "classifies every line to a taxonomy.",
        "AI-driven spend classification learns from historical coding to assign categories "
        "to millions of transactions.",
    ],
    P2P: [
        "Procure-to-pay (P2P) automates requisitions, approvals, purchase orders, receiving "
        "and invoice matching.",
        "Three-way invoice matching compares the invoice, the purchase order and the goods "
        "receipt before payment.",
    ],
    SUPPLIER: [
        "Digital supplier risk management combines onboarding workflows with continuous "
        "monitoring of financial, ESG and cyber signals.",
        "Supplier management platforms centralise supplier data, performance scorecards and "
        "risk alerts.",
    ],
    REVIEWS: [
        "Analyst reviews and peer ratings tend to compare procurement suites on usability, "
        "breadth and total cost of ownership.",
        "Enterprise buyers typically shortlist two or three source-to-pay suites and run a "
        "scripted demo.",
    ],
    AI: [
        "Generative AI in procurement drafts sourcing events, summarises supplier proposals "
        "and answers policy questions in natural language.",
        "Procurement AI agents can create RFx documents, recommend suppliers and flag "
        "contract deviations.",
    ],
    IMPL: [
        "Implementation timelines depend on the number of ERP integrations, regions and "
        "legacy catalogues to migrate.",
        "Pricing for enterprise procurement suites is usually quoted per module and per "
        "managed spend.",
    ],
}

CLIENT_SENTENCES: Final[dict[str, list[str]]] = {
    S2P: [
        "GEP SMART is a unified source-to-pay platform that covers sourcing, contracts, "
        "procure-to-pay and spend analysis in one application.",
        "GEP SMART is frequently shortlisted by global manufacturers for its single data "
        "model across upstream and downstream procurement.",
    ],
    SPEND: [
        "GEP SMART uses AI-based classification to map spend to a category taxonomy with "
        "minimal manual coding.",
        "GEP includes spend analysis as a native module rather than a separate reporting tool.",
    ],
    P2P: [
        "GEP SMART supports guided buying, catalogue purchasing and touchless invoice "
        "matching in its procure-to-pay module.",
        "GEP SMART handles requisition-to-payment end to end, including supplier invoicing "
        "portals.",
    ],
    SUPPLIER: [
        "GEP SMART scores supplier risk automatically from third-party feeds and internal "
        "performance data.",
        "GEP offers supplier onboarding with risk scoring built into the same workflow.",
    ],
    REVIEWS: [
        "GEP SMART is praised in reviews for its unified suite and criticised occasionally "
        "for configuration effort.",
        "Reviewers often place GEP SMART alongside Coupa and Ivalua as an enterprise-grade option.",
    ],
    AI: [
        "GEP applies generative AI across GEP SMART and GEP NEXXE to draft sourcing events "
        "and summarise supplier responses.",
        "GEP SMART includes an AI assistant that answers policy and contract questions "
        "inside the buying flow.",
    ],
    IMPL: [
        "GEP SMART implementations typically run three to nine months depending on ERP "
        "integrations and regions.",
        "GEP SMART integrates with SAP S/4HANA and Oracle ERP through packaged connectors.",
    ],
}

COMPETITOR_SENTENCES: Final[dict[str, str]] = {
    "Coupa": (
        "Coupa is often cited for its business spend management approach and supplier community."
    ),
    "SAP Ariba": (
        "SAP Ariba remains the default choice for organisations already standardised on SAP."
    ),
    "Oracle": (
        "Oracle Procurement Cloud appeals to Oracle ERP customers because of native integration."
    ),
    "Jaggaer": "Jaggaer is strong in direct materials sourcing and higher-education procurement.",
    "Ivalua": "Ivalua is known for configurability and a single-platform architecture.",
}

CLOSERS: Final[list[str]] = [
    "Buyers should compare integration depth, AI-assisted classification and total cost of "
    "ownership before shortlisting.",
    "The right choice depends on ERP landscape, category mix and how much configuration the "
    "team can own.",
    "Most evaluations end with a scripted demo on the organisation's own spend data.",
]

# Third-party pages the engines cite (url, title); the engine classifies the domain itself.
THIRD_PARTY_PAGES: Final[dict[str, list[tuple[str, str]]]] = {
    S2P: [
        ("https://www.gartner.com/reviews/market/source-to-pay-suites", "Source-to-Pay Suites"),
        ("https://www.procurementmag.com/articles/what-is-source-to-pay", "What is S2P?"),
        ("https://www.techtarget.com/searcherp/definition/source-to-pay", "S2P definition"),
    ],
    SPEND: [
        ("https://www.forbes.com/advisor/business/spend-analysis-software/", "Best spend tools"),
        ("https://spendmatters.com/spend-analysis-explained", "Spend analysis explained"),
        ("https://www.g2.com/categories/spend-analysis", "Best Spend Analysis Software - G2"),
    ],
    P2P: [
        ("https://www.capterra.com/procure-to-pay-software/", "Procure to Pay Software"),
        ("https://www.techtarget.com/searcherp/definition/procure-to-pay", "P2P definition"),
        ("https://www.reddit.com/r/procurement/comments/p2p_tools", "What P2P tool do you use?"),
    ],
    SUPPLIER: [
        (
            "https://www.gartner.com/reviews/market/supplier-risk-management-solutions",
            "Best Supplier Risk Management Solutions",
        ),
        ("https://www.ifs.com/en/glossary/supplier-risk-management", "Supplier Risk Management"),
        ("https://www.kodiakhub.com/blog/supplier-risk-management", "Supplier risk strategies"),
    ],
    REVIEWS: [
        ("https://www.g2.com/products/gep-smart/reviews", "GEP SMART Reviews - G2"),
        ("https://www.capterra.com/p/gep-smart/", "GEP SMART Reviews - Capterra"),
        ("https://www.gartner.com/reviews/market/procure-to-pay-suites", "P2P Suites - Gartner"),
        ("https://www.trustradius.com/products/gep-smart/reviews", "GEP SMART - TrustRadius"),
    ],
    AI: [
        (
            "https://www.mckinsey.com/capabilities/operations/our-insights/generative-ai",
            "Generative AI in procurement",
        ),
        ("https://www.procurementaiagents.com/guide", "Procurement AI agents guide"),
        ("https://www.forbes.com/sites/ai-in-procurement", "How AI is reshaping procurement"),
    ],
    IMPL: [
        ("https://www.erpresearch.com/gep-smart-implementation", "GEP SMART implementation"),
        ("https://appsource.microsoft.com/en-us/product/gep-smart", "GEP SMART on AppSource"),
        ("https://www.procurementmag.com/articles/procurement-software-pricing", "Pricing"),
    ],
}

# url, title, competitor name
COMPETITOR_PAGES: Final[dict[str, tuple[str, str, str]]] = {
    "coupa.com": ("https://www.coupa.com/products/procure-to-pay", "P2P | Coupa", "Coupa"),
    "sap.com": (
        "https://www.sap.com/products/spend-management/ariba.html",
        "SAP Ariba",
        "SAP Ariba",
    ),
    "oracle.com": ("https://www.oracle.com/erp/procurement/", "Oracle Procurement Cloud", "Oracle"),
    "jaggaer.com": ("https://www.jaggaer.com/solutions/source-to-pay/", "S2P | Jaggaer", "Jaggaer"),
    "ivalua.com": ("https://www.ivalua.com/platform/source-to-pay/", "S2P | Ivalua", "Ivalua"),
}

FANOUT: Final[dict[str, list[str]]] = {
    S2P: [
        "source to pay vs procure to pay",
        "GEP SMART source-to-pay features",
        "site:gep.com source to pay",
    ],
    SPEND: [
        "AI spend classification procurement",
        "best spend analysis tools manufacturing",
        "GEP SMART spend analysis",
    ],
    P2P: [
        "procure to pay software mid-market",
        "three way invoice matching automation",
        "coupa procure to pay",
    ],
    SUPPLIER: [
        "supplier risk management software",
        "supplier onboarding risk scoring",
        "GEP supplier risk",
    ],
    REVIEWS: [
        "GEP SMART reviews G2",
        "coupa vs ivalua vs gep smart",
        "top procurement software 2026",
    ],
    AI: [
        "generative AI procurement use cases",
        "procurement AI agents sourcing events",
        "GEP NEXXE AI",
    ],
    IMPL: [
        "GEP SMART implementation timeline",
        "GEP SMART pricing",
        "GEP SMART SAP S/4HANA integration",
    ],
}

PAA: Final[dict[str, list[str]]] = {
    S2P: [
        "What is the difference between S2P and P2P?",
        "Which companies use GEP SMART?",
        "Is source-to-pay part of ERP?",
    ],
    SPEND: [
        "How does spend analysis work?",
        "What is spend classification?",
        "Which tool is best for spend analysis?",
    ],
    P2P: [
        "What is three-way matching?",
        "What is the best P2P software?",
        "How does P2P automation reduce cost?",
    ],
    SUPPLIER: [
        "How do you score supplier risk?",
        "What is supplier onboarding software?",
        "Which vendors offer supplier risk monitoring?",
    ],
    REVIEWS: [
        "Is GEP SMART better than Coupa?",
        "Who are the top procurement software vendors?",
        "What do users say about Ivalua?",
    ],
    AI: [
        "How is AI used in procurement?",
        "What are procurement AI agents?",
        "Can AI write an RFP?",
    ],
    IMPL: [
        "How much does GEP SMART cost?",
        "How long does a GEP implementation take?",
        "Does GEP integrate with SAP?",
    ],
}

RELATED: Final[dict[str, list[str]]] = {
    S2P: ["source to pay software gartner", "s2p suite comparison", "gep smart pricing"],
    SPEND: ["spend analysis software free", "spend cube", "gep spend analysis"],
    P2P: ["p2p software list", "coupa p2p", "invoice automation tools"],
    SUPPLIER: [
        "supplier risk software gartner",
        "vendor onboarding tools",
        "gep supplier management",
    ],
    REVIEWS: ["gep smart g2", "ivalua reviews", "coupa reviews"],
    AI: ["ai procurement software", "gep nexxe", "procurement copilot"],
    IMPL: ["gep smart cost", "gep smart demo", "gep smart sap integration"],
}

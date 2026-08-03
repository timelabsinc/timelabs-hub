#!/usr/bin/env python3
"""Shared writing brief and editorial lint for TimeLabs content tools.

This module does not detect AI authorship. It flags patterns for editorial
review and blocks only residue or sourcing problems that should never ship.
Keep it stdlib-only: agent_chat_server imports adjacent content modules on the
system Python runtime.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    code: str
    pattern: str
    message: str
    severity: str = "review"
    channels: tuple = ()


# These preserve the deterministic Reddit gate's existing behaviour while
# giving every content surface one source of truth. A phrase match is a signal
# to rewrite the sentence, never proof that a model wrote it.
PHRASE_RULES = (
    Rule("phrase.abstract.delve", r"\bdelv(e|es|ed|ing)\b", "replace abstract exploration with the actual action"),
    Rule("phrase.abstract.tapestry", r"\b(?:rich\s+)?tapestry\b", "name the concrete mix or history"),
    Rule("phrase.inflated.testament", r"\btestament to\b", "state what the evidence demonstrates"),
    Rule("phrase.promo.elevate", r"\belevat(e|es|ing|ed)\b", "name what improves and how"),
    Rule("phrase.promo.seamless", r"\bseamless(?:ly)?\b", "describe the transition or fit"),
    Rule("phrase.promo.robust", r"\brobust\b", "replace broad quality language with evidence"),
    Rule("phrase.promo.leverage", r"\bleverag(?:e|es|ed|ing)\b", "use an ordinary verb"),
    Rule("phrase.promo.unlock", r"\bunlock(?:s|ed|ing)?\b", "state the result directly"),
    Rule("phrase.promo.empower", r"\bempower(?:s|ed|ing)?\b", "state what the person can do"),
    Rule("phrase.promo.game_changer", r"\bgame[- ]chang(?:er|ing)\b", "prove the change or remove the claim"),
    Rule("phrase.abstract.navigate", r"\bnavigat(?:e|es|ed|ing) (?:the )?(?:complexities|landscape|world)\b", "name the actual constraint"),
    Rule("intro.generic.today", r"\bin today'?s (?:world|market|landscape|fast[- ]paced world)\b", "start with the event, fact or object"),
    Rule("phrase.transition.note", r"\bit'?s (?:worth|important to) not(?:e|ing)\b", "state the point without announcing it"),
    Rule("phrase.transition.that_said", r"\bthat said,", "use the actual contrast or delete the transition"),
    Rule("phrase.transition.moreover", r"\bmoreover,", "delete the essay transition"),
    Rule("phrase.transition.furthermore", r"\bfurthermore,", "delete the essay transition"),
    Rule("outro.generic.conclusion", r"\bin conclusion\b", "end on the natural next beat"),
    Rule("phrase.transition.end_of_day", r"\bat the end of the day\b", "state the decision directly"),
    Rule("phrase.abstract.when_it_comes", r"\bwhen it comes to\b", "begin with the specific subject"),
    Rule("structure.parallelism.not_just", r"\bnot just .{1,55}? but(?: also)?\b", "replace manufactured contrast with a fact"),
    Rule("structure.parallelism.isnt_just", r"\bisn'?t just .{1,55}? it'?s\b", "replace manufactured significance with a fact"),
    Rule("structure.parallelism.more_than", r"\bmore than just\b", "say what else it is and prove it"),
    Rule("intro.generic.whether", r"\bwhether you'?re\b", "address the actual reader or remove the audience sweep"),
    Rule("phrase.abstract.dive", r"\bdive (?:deep )?into\b", "start the subject directly"),
    Rule("phrase.promo.crafted", r"\bcrafted\b", "name the build work instead"),
    Rule("phrase.promo.meticulous", r"\bmeticulous(?:ly)?\b", "show the precise work instead"),
    Rule("phrase.inflated.stands", r"\bstands? as\b", "use is, has or a concrete verb"),
    Rule("phrase.promo.boasts", r"\bboasts?\b", "use has or name the feature"),
    Rule("phrase.abstract.core", r"\bat (?:its|the) core\b", "state the central fact directly"),
    Rule("phrase.abstract.realm", r"\bin the realm of\b", "name the subject directly"),
    Rule("phrase.inflated.underscores", r"\b(?:underscores?|highlights?) the (?:importance|need|significance)\b", "explain the consequence with evidence"),
    Rule("phrase.promo.blend", r"\b(?:perfect|seamless) (?:blend|fusion)\b", "name the two elements and their visible relationship"),
)

TELL_PATTERNS = tuple(rule.pattern for rule in PHRASE_RULES)

BLOCKING_RULES = (
    Rule("residue.ai_identity", r"\bas an ai(?: language model)?\b", "remove AI self-reference", "block"),
    Rule("residue.offer_more", r"\blet me know if you(?: would|'d) like\b", "remove assistant follow-up offer", "block"),
    Rule("residue.polished_version", r"\bhere(?:'s| is) (?:a |the )?(?:polished|revised|humanized|humanised) (?:version|draft)\b", "remove assistant narration", "block"),
    Rule("residue.below_is", r"\bbelow is (?:a |the )?(?:draft|post|caption|email|article|script)\b", "remove assistant narration", "block"),
    Rule("residue.placeholder", r"(?:\[(?:insert|add|your)[^\]\n]{0,50}\]|<(?:insert|company|name|link|detail)[^>\n]{0,40}>)", "replace or remove placeholder copy", "block"),
    Rule("claim.vague_source", r"\b(?:experts say|studies show|research (?:shows|suggests|indicates)|many believe|it is widely (?:known|believed))\b", "name and verify the source or remove the claim", "block"),
)

REVIEW_RULES = (
    Rule("intro.generic.imagine", r"(?:^|\n)\s*(?:imagine|picture this)\b", "open on the real scene, object or result"),
    Rule("intro.generic.wondered", r"(?:^|\n)\s*have you ever wondered\b", "answer directly unless a real answer is expected"),
    Rule("intro.generic.introducing", r"(?:^|\n)\s*introducing\b", "name what is new and why it was built"),
    Rule("intro.generic.thrilled", r"\bwe (?:are|'re) (?:thrilled|excited|delighted) to (?:announce|unveil|introduce)\b", "state the announcement and useful detail"),
    Rule("outro.generic.future", r"\bthe future (?:looks|is) bright\b", "end with the result, decision or next experiment"),
    Rule("outro.generic.only_time", r"\bonly time will tell\b", "state what will be tested or decided"),
    Rule("outro.generic.feedback", r"\blet (?:us|me) know what you think\b", "ask one specific question or end earlier"),
)

CHANNEL_GUIDANCE = {
    "reddit": (
        "Write as a useful participant, not a brand account performing expertise. "
        "Use the exact part, visible detail, failed attempt or build decision. Keep it short. "
        "No hashtags, sales CTA, feature dump or generic praise. End only with a question a builder would genuinely answer."
    ),
    "instagram": (
        "Choose one job for the caption: reveal a build choice, explain a component, answer an objection, "
        "show a before/after, tell a customer story or ask a precise preference. Add what the image cannot show. "
        "Do not stack luxury adjectives, emoji or generic DM calls."
    ),
    "email": (
        "State the reason early, include only decision-relevant context, make one clear request and close naturally. "
        "Acknowledge the customer's actual issue before policy. Avoid formal filler and repeated summaries."
    ),
    "whatsapp": (
        "Use short natural turns, one request at a time and the customer's actual name/details. "
        "Do not turn a message into an email or hide the answer behind a long preamble."
    ),
    "blog": (
        "Answer the search question early. Use first-party measurements, build records, failures, photos and sources. "
        "Delete generic history, keyword-shaped padding and sections that exist only for length."
    ),
    "youtube": (
        "Write for breath and footage. Open on the object, conflict or result; mark what should be shown. "
        "Do not repeatedly preview or recap the video. End after the answer or next experiment."
    ),
    "product": (
        "Lead with the distinguishing build detail, then accurate movement, case, dial, size, finish and delivery facts. "
        "No luxury filler, unsupported superlatives, fake scarcity or factory-Seiko implication."
    ),
    "founder": (
        "Start from a real decision, mistake, customer moment or number. Keep the awkward specific detail. "
        "Avoid manufactured confession, stacked hooks, false vulnerability and a universal founder moral."
    ),
}

BASE_BRIEF = (
    "TimeLabs human-writing standard: write from verified facts and one specific point of view. "
    "Choose one route and one job before drafting. Prefer ordinary verbs, concrete nouns and details unique to this item. "
    "Vary rhythm because the thought changes, not by formula. Do not inflate significance, restate the previous sentence, "
    "manufacture three-part cadence, use vague authority or add facts during a style rewrite. Match the native shape of the channel. "
    "End on the natural next action or unresolved detail, not a summary. After editing, compare every claim with the source and read it aloud."
)


def prompt_brief(channel="general"):
    """Return the concise standard content generators should receive up front."""
    key = (channel or "general").strip().lower()
    guidance = CHANNEL_GUIDANCE.get(key, "Use the shortest native shape that completes the reader's job.")
    return BASE_BRIEF + "\nChannel rule: " + guidance


def lint(text, channel="general"):
    """Return structured editorial flags; matches are not authorship verdicts."""
    src = text or ""
    rules = BLOCKING_RULES + REVIEW_RULES + PHRASE_RULES
    flags = []
    for rule in rules:
        if rule.channels and channel not in rule.channels:
            continue
        match = re.search(rule.pattern, src, re.I | re.M)
        if match:
            flags.append({
                "code": rule.code,
                "severity": rule.severity,
                "match": match.group(0).strip()[:120],
                "message": rule.message,
            })

    questions = re.findall(r"[^?\n]{1,180}\?", src)
    if len(questions) >= 3:
        flags.append({
            "code": "structure.rule_of_three.questions",
            "severity": "review",
            "match": f"{len(questions)} questions",
            "message": "keep only questions the reader is genuinely expected to answer",
        })

    transitions = re.findall(
        r"(?:^|\n)\s*(?:Moreover|Furthermore|Additionally|In addition|Ultimately|Overall),?\b",
        src, re.I)
    if len(transitions) >= 2:
        flags.append({
            "code": "structure.transition_stack",
            "severity": "review",
            "match": f"{len(transitions)} paragraph transitions",
            "message": "replace essay scaffolding with the actual relationship between ideas",
        })
    return flags


def blocking_problems(text, channel="general", include_phrases=False):
    """Return human-readable failures for deterministic publication gates."""
    flags = lint(text, channel)
    selected = [f for f in flags if f["severity"] == "block"]
    if include_phrases:
        selected.extend(f for f in flags if f["code"].startswith(("phrase.", "structure.parallelism")))
    return [f"{flag['code']}: {flag['match']}" for flag in selected]

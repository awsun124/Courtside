from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
import re
import time
from zoneinfo import ZoneInfo

from scripts.validate_gdelt import canonical_url
from .stories import clean_text


@dataclass
class InvestigationSettings:
    provider: str = "rules"
    max_stories: int = 10
    max_steps: int = 80
    max_steps_per_story: int = 8
    max_seconds: float = 15
    max_articles_per_story: int = 6

    def validate(self):
        if self.provider != "rules":
            raise ValueError("Only the rules-based investigator is supported")
        limits = {"max_stories": 10, "max_steps": 100, "max_steps_per_story": 12,
                  "max_articles_per_story": 8}
        for name, maximum in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be between 1 and {maximum}")
        if type(self.max_seconds) not in (float, int) or not math.isfinite(self.max_seconds) or not 0 < self.max_seconds <= 30:
            raise ValueError("Investigation time must be positive and no more than 30 seconds")


def evidence_id(url):
    return "article-" + hashlib.sha256(canonical_url(url).encode()).hexdigest()[:16]


def short_text(text, limit=1200):
    return " ".join((text or "").split())[:limit]


def suspicious_text(text):
    """Detect attempts to direct the reader's tools or generated answer.

    Clearly attributed quotations can discuss an attack without issuing it.
    Commands outside those quotations are still checked. This is a heuristic;
    tool permissions and evidence checks remain the enforcement boundaries.
    """
    quoted = re.compile(r'(?P<quote>["“])(?P<body>[^"“”]*?)["”]')
    def reported_quote(match):
        prefix = text[max(0, match.start()-160):match.start()]
        # Require a reporting verb in the same sentence before the quotation.
        if re.search(r'\b(?:said|reported|warned|explained|described|quoted|wrote)\b[^.!?"“”]{0,120}$', prefix, re.I):
            return ' [reported quotation] '
        return match.group(0)
    remaining = clean_text(quoted.sub(reported_quote, text or ''))
    direct = (r'\bignore (?:all |previous |earlier )?instructions\b',
              r'\breveal your\b', r'\b(?:reveal|print|send|expose)\b.{0,60}\b(?:api key|system prompt|credentials)\b',
              r'\bexecute this\b', r'\brun shell\b', r'\bcall this tool\b',
              r'\breplace (?:the |this )?(?:report|summary|answer|response)\b',
              r'\b(?:next|final) (?:answer|response) must\b',
              r'\b(?:do not|don t) (?:mention|disclose) this (?:note|instruction|directive)\b',
              r'\bconceal this (?:note|instruction|directive)\b',
              r'\boverrides? (?:all |the )?(?:normal |previous |earlier )?(?:fact checks|instructions|rules)\b')
    if any(re.search(pattern, remaining) for pattern in direct):
        return True
    target = re.search(r'\b(?:downstream writer|ai assistant|automated summarizer|your response)\b', remaining)
    redirect = re.search(r'\b(?:announce|invent|fabricate|replace|output|override|highest priority)\b', remaining)
    return bool(target and redirect)


def publication_time(value):
    """Return a dated source time, or None when the date cannot be established."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def availability_game_date(claim, published):
    """Resolve an explicit date or an unambiguous publication-relative day."""
    days = set()
    for value in re.findall(r'\b20\d{2}-\d{2}-\d{2}\b', claim):
        try: days.add(datetime.fromisoformat(value).date())
        except ValueError: return None
    months = ('january february march april may june july august september october november december').split()
    names = {name: index+1 for index,name in enumerate(months)}
    names.update({name[:3]: index+1 for index,name in enumerate(months)})
    pattern = r'\b('+'|'.join(sorted(names,key=len,reverse=True))+r')\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b'
    for month, day, year in re.findall(pattern, claim.lower()):
        if not year and published is None: return None
        years = [int(year)] if year else [published.year-1,published.year,published.year+1]
        candidates = []
        for candidate_year in years:
            try: candidates.append(datetime(candidate_year,names[month],int(day)).date())
            except ValueError: continue
        if not candidates: return None
        days.add(min(candidates,key=lambda date:abs((date-published.date()).days)) if not year else candidates[0])
    for relative in re.findall(r'\b(tonight|today|tomorrow)\b',claim.lower()):
        if published is None: return None
        days.add(published.date()+timedelta(days=int(relative=='tomorrow')))
    return next(iter(days)) if len(days)==1 else None


def availability_assessment(retrieved, allowed_ids, reporting_groups=()):
    """Compare explicit statuses; only a later same-group correction supersedes a report."""
    claims = {}
    group_of = {evidence_id(url):index for index,group in enumerate(reporting_groups) for url in group['evidence_urls']}
    pattern = re.compile(r"(?P<name>[A-Z][\w’'-]+(?:\s+[A-Z][\w’'-]+){1,3})\s+"
                         r"(?:is|was|remains|will|would|has been)\s+(?P<claim>[^!?]+?)(?=(?<!Jan)(?<!Feb)(?<!Mar)(?<!Apr)(?<!Jun)(?<!Jul)(?<!Aug)(?<!Sep)(?<!Oct)(?<!Nov)(?<!Dec)\.\s|\.$|$)")
    for evidence, material in retrieved.items():
        if evidence not in allowed_ids or material.get('quarantined'): continue
        published = publication_time(material.get('published_at'))
        published = published.astimezone(ZoneInfo('America/New_York')) if published else None
        texts = material.get('material', {})
        text = texts.get('excerpt') or texts.get('description') or texts.get('title') or ''
        correction = bool(re.search(r'\bcorrection to our earlier report\b|\bwe (?:correct|withdraw|retract) our earlier report\b',text,re.I))
        for match in pattern.finditer(text):
            claim = match['claim'].lower()
            game_day = availability_game_date(claim,published)
            if game_day is None: continue
            if re.match(r"(?:not (?:ruled out|out)|(?:not )?expected to play|questionable|doubtful|(?:a )?game[- ]time decision)\b",claim): status='uncertain'
            elif re.match(r"(?:out|ruled out|miss|not play|not be playing|unable to play)\b",claim): status='out'
            elif re.match(r"(?:available|cleared to play|play)\b",claim): status='available'
            else: continue
            key=(match['name'].casefold(),game_day.isoformat())
            entry=dict(player=match['name'],evidence_id=evidence,status=status)
            entries=claims.setdefault(key,[])
            if not any(item['evidence_id']==evidence and item['status']==status for item in entries):
                entries.append(dict(**entry,published=published,correction=correction,group=group_of.get(evidence,evidence)))
    disagreements=[];corrections=[];replacements={}
    for (_,day),entries in claims.items():
        active=[]
        for old in entries:
            newer=[new for new in entries if new['correction'] and new['group']==old['group']
                   and new['evidence_id']!=old['evidence_id'] and new['published'] and old['published']
                   and new['published']>old['published']]
            if newer:
                latest=max(newer,key=lambda entry:entry['published'])
                replacements[old['evidence_id']]=latest['evidence_id']
                corrections.append(dict(player=old['player'],game_date=day,
                    superseded_evidence_id=old['evidence_id'],correction_evidence_id=latest['evidence_id'],
                    previous_status=old['status'],corrected_status=latest['status'],resolution='source_correction'))
            else: active.append(old)
        if any(a['evidence_id']!=b['evidence_id'] and a['status']!=b['status'] for a in active for b in active):
            disagreements.append(dict(kind='player_availability',player=active[0]['player'],game_date=day,
                claims=[{k:v for k,v in entry.items() if k in {'player','evidence_id','status'}} for entry in active],resolution='unresolved'))
    return dict(disagreements=disagreements,corrections=corrections,replacements=replacements)


def availability_disagreements(retrieved, allowed_ids):
    return availability_assessment(retrieved,allowed_ids)['disagreements']


class EvidenceTools:
    def __init__(self, result, briefing, previous):
        self.articles = {evidence_id(article["url"]): article for article in result["articles"]}
        self.groups = {story["id"]: story for story in result["stories"]}
        self.candidates = {story["id"]: story for story in briefing["candidates"]}
        self.previous = previous
        self.mode = briefing.get("data_mode", "live")

    def article_ids(self, story_id):
        return [evidence_id(url) for url in self.groups[story_id]["article_urls"]]

    def article_label(self, article_id):
        article = self.articles[article_id]
        published = publication_time(article.get('published_at'))
        return dict(evidence_id=article_id, title=short_text(article["title"], 200),
                    publisher=article["publisher_name"], url=article["url"],
                    published_at=published.isoformat() if published else None, untrusted=True)

    def search_articles(self, query, limit):
        if not isinstance(query, str) or not 1 <= len(query) <= 200 or type(limit) is not int or not 1 <= limit <= 5:
            raise ValueError("Search needs a short query and a limit from 1 to 5")
        words = set(clean_text(query).split())
        matches = []
        for article_id, article in self.articles.items():
            if article.get("decision") == "excluded" or article.get("investigation_allowed") is False:
                continue
            text = clean_text(article["title"] + " " + (article.get("description") or ""))
            overlap = len(words & set(text.split()))
            if overlap:
                matches.append((overlap, article_id))
        matches.sort(key=lambda item: (-item[0], item[1]))
        return dict(matches=[self.article_label(article_id) for _, article_id in matches[:limit]],
                    scope="Saved article metadata only")

    def retrieve_material(self, article_id):
        if article_id not in self.articles:
            raise ValueError("Evidence must refer to a collected article ID")
        article = self.articles[article_id]
        if article.get("decision") == "excluded":
            raise ValueError("Excluded articles are not available as reporting evidence")
        if article.get("investigation_allowed") is False:
            raise ValueError("This source is available as a headline and link only")
        texts = {name: short_text(article.get(field)) for name, field in
                 [("title", "title"), ("description", "description"), ("excerpt", "content_excerpt")]}
        quarantined = any(suspicious_text(text) for text in texts.values())
        return dict(**self.article_label(article_id), material_scope="Saved API excerpt, not full article text",
                    material={} if quarantined else texts, quarantined=quarantined)

    def inspect_related_stories(self, story_id):
        if story_id not in self.groups:
            raise ValueError("Unknown story ID")
        target = self.groups[story_id]
        target_features = [self.articles[evidence_id(url)].get("features", {}) for url in target["article_urls"]]
        teams = {team for features in target_features for team in features.get("teams", [])}
        people = {person for features in target_features for person in features.get("people", [])}
        related = []
        for other in self.groups.values():
            if other["id"] == story_id:
                continue
            features = [self.articles[evidence_id(url)].get("features", {}) for url in other["article_urls"]]
            if any(teams & set(item.get("teams", [])) or people & set(item.get("people", [])) for item in features):
                related.append(dict(story_id=other["id"], title=short_text(other["title"], 200),
                                    event=other["event"], evidence_ids=self.article_ids(other["id"]),
                                    relation="Shared names or teams, not necessarily the same event"))
        return dict(story_id=story_id, event=target["event"], evidence_ids=self.article_ids(story_id),
                    related=related[:5])

    def compare_previous_briefings(self, story_id):
        if story_id not in self.candidates:
            raise ValueError("Unknown story ID")
        current = self.candidates[story_id]
        urls = {canonical_url(url) for url in current["evidence_urls"]}
        for briefing in self.previous[:3]:
            if briefing.get("data_mode", "live") != self.mode:
                continue
            for old in briefing["candidates"]:
                old_urls = {canonical_url(url) for url in old["evidence_urls"]}
                if old["id"] != story_id and not urls & old_urls:
                    continue
                return dict(status="compared", previous_briefing_id=briefing.get("briefing_id"),
                            previous_as_of=briefing["as_of"], new_source_urls=sorted(urls - old_urls),
                            removed_source_urls=sorted(old_urls - urls),
                            publisher_group_change=current["breakdown"]["coverage"]["independent_publishers"] - old["breakdown"]["coverage"]["independent_publishers"],
                            score_change=current["score"] - old["score"], meaningful_update="not established",
                            note="New links or score changes alone do not show that the story has changed")
        return dict(status="no_previous_match", meaningful_update="not established",
                    note="No matching story found in the last three saved briefings. This does not prove the event is new.")

    def call(self, name, arguments):
        allowed = {"search_articles": {"query", "limit"}, "retrieve_material": {"article_id"},
                   "inspect_related_stories": {"story_id"}, "compare_previous_briefings": {"story_id"}}
        if name not in allowed or not isinstance(arguments, dict) or set(arguments) != allowed[name]:
            raise ValueError("Unknown tool or unsupported arguments")
        return getattr(self, name)(**arguments)


def source_excerpt(text, limit=240):
    sentences = [match.end() for match in re.finditer(r'[.!?]["’”\']?(?=\s|$)', text[:limit + 1]) if match.end() <= limit]
    if sentences:
        return text[:sentences[-1]].strip()
    if len(text) <= limit:
        return text.strip()
    cutoff = text.rfind(" ", 0, limit + 1)
    return text[:cutoff if cutoff > 0 else limit].rstrip()


def choose_source_excerpt(material):
    """Prefer a complete saved sentence; never supply missing words."""
    excerpts = [source_excerpt(material[field]) for field in ('description', 'excerpt') if material.get(field)]
    for quote in excerpts:
        if re.search(r'[.!?]["’”\']?$', quote) and not quote.endswith('...'):
            return quote
    return next(iter(excerpts), source_excerpt(material.get('title') or ''))


class RulesProvider:
    name = "rules"

    def start(self, context):
        pass

    def choose(self, state, timeout):
        story_id = state["story_id"]
        for tool in ["inspect_related_stories", "compare_previous_briefings"]:
            if tool not in state["results"]:
                return dict(name=tool, arguments=dict(story_id=story_id))
        if "search_articles" not in state["results"]:
            return dict(name="search_articles", arguments=dict(query=state["query"], limit=5))
        remaining = state["priority_ids"] + [item["evidence_id"] for item in state["results"]["search_articles"]["matches"]]
        if state["steps_left"] > 1 and len(state["retrieved"]) < state["article_limit"]:
            for article_id in remaining:
                if article_id not in state["retrieved"]:
                    return dict(name="retrieve_material", arguments=dict(article_id=article_id))
        claims = []
        for article_id in state["summary_ids"]:
            material = state["retrieved"].get(article_id)
            if material and not material["quarantined"]:
                quote = choose_source_excerpt(material['material'])
                if quote:
                    claims.append(dict(evidence_id=article_id, quote=quote))
            if len(claims) == 2:
                break
        return dict(name="finish", arguments=dict(claims=claims))

    def observe(self, action, result):
        pass


def supported_summary(claims, retrieved, allowed_ids):
    if not isinstance(claims, list) or len(claims) > 3:
        raise ValueError("A summary can contain at most three evidence excerpts")
    citations, lines, displayed_quotes = [], [], set()
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {"evidence_id", "quote"}:
            raise ValueError("Summary claims must contain only an evidence ID and quote")
        article_id, quote = claim["evidence_id"], claim["quote"]
        material = retrieved.get(article_id)
        if (article_id not in allowed_ids or material is None or material["quarantined"] or
                not isinstance(quote, str) or not 5 <= len(quote) <= 300 or
                not any(quote in text for text in material["material"].values())):
            raise ValueError("Summary text must come from retrieved, in-story evidence")
        ending = "" if re.search(r'[.!?…]["’”\']?$', quote) else "…"
        key = ' '.join(quote.split())
        if key not in displayed_quotes:
            lines.append(f"{material['publisher']} reports: {quote}{ending}")
            displayed_quotes.add(key)
        citations.append(dict(evidence_id=article_id, url=material["url"], publisher=material["publisher"],
                              quote=quote, published_at=material["published_at"]))
    if not citations:
        return None
    return dict(kind="source_excerpts", text="\n".join(lines), citations=citations,
                note="Excerpts from the linked reports. We have not independently verified their claims.")


def investigate_briefing(result, briefing, previous=None, settings=None, provider=None, clock=time.monotonic):
    settings = settings or InvestigationSettings()
    settings.validate()
    if provider is None:
        provider = RulesProvider()
    tools = EvidenceTools(result, briefing, previous or [])
    started_at = clock()
    deadline, steps = started_at + settings.max_seconds, 0
    records = {}
    for position, candidate in enumerate(briefing["candidates"]):
        story_id = candidate["id"]
        record = dict(provider=provider.name, status="limited", summary=None, activity=[], evidence_references=[],
                      what_changed=None, uncertainty=["Investigation limits reached before this story was completed"],
                      corroboration=dict(retrieved_reporting_groups=0, status="not_checked"),
                      decision_summary="Investigation limits reached before this story was completed")
        records[story_id] = record
        if position >= settings.max_stories or steps >= settings.max_steps or clock() >= deadline:
            continue
        target_ids = tools.article_ids(story_id)
        primary_ids = [evidence_id(group["evidence_urls"][0]) for group in candidate["reporting_groups"]]
        priority = list(dict.fromkeys(primary_ids + target_ids))
        first = tools.articles[target_ids[0]] if target_ids else {}
        query = " ".join(first.get("features", {}).get("people", [])) or short_text(candidate["title"], 200)
        state = dict(story_id=story_id, query=query, priority_ids=priority, summary_ids=primary_ids, results={}, retrieved={},
                     article_limit=settings.max_articles_per_story, steps_left=settings.max_steps_per_story)
        provider.start(dict(story_id=story_id, title=short_text(candidate["title"], 200), evidence_ids=target_ids,
                            data_mode=tools.mode, untrusted=True))
        for story_step in range(settings.max_steps_per_story):
            if steps >= settings.max_steps or clock() >= deadline:
                break
            state["steps_left"] = min(settings.max_steps_per_story - story_step, settings.max_steps - steps)
            try:
                action = provider.choose(state, max(0.01, deadline - clock()))
                if clock() >= deadline:
                    break
                steps += 1
                name, arguments = action["name"], action["arguments"]
                if name == "finish":
                    if not isinstance(arguments, dict) or set(arguments) != {"claims"}:
                        raise ValueError("Finish accepts only cited excerpts")
                    record["summary"] = supported_summary(arguments["claims"], state["retrieved"], set(target_ids))
                    record["status"] = "complete" if record["summary"] else "insufficient_evidence"
                    record["activity"].append(dict(step=story_step + 1, tool="finish", status="ok",
                                                   evidence_ids=[claim["evidence_id"] for claim in arguments["claims"]],
                                                   decision="Finished with excerpts checked against the saved sources"))
                    break
                if name == "retrieve_material" and len(state["retrieved"]) >= settings.max_articles_per_story:
                    raise ValueError("Article retrieval limit reached")
                tool_result = tools.call(name, arguments)
                if name in {"inspect_related_stories", "compare_previous_briefings"} and arguments["story_id"] != story_id:
                    raise ValueError("Story inspection must stay within the current investigation")
                if name == "retrieve_material":
                    state["retrieved"][arguments["article_id"]] = tool_result
                else:
                    state["results"][name] = tool_result
                record["activity"].append(dict(step=story_step + 1, tool=name, arguments=arguments, status="ok",
                                               evidence_ids=[arguments["article_id"]] if name == "retrieve_material" else [],
                                               decision="Retrieved saved excerpt" if name == "retrieve_material" else "Checked saved article details"))
                provider.observe(action, tool_result)
            except Exception:
                record["status"] = "unavailable"
                record["activity"].append(dict(step=story_step + 1, tool="rejected_action", status="rejected",
                                               decision="Stopped because the provider returned an unsupported answer or tool request"))
                break
        record["evidence_references"] = [dict(evidence_id=article_id, url=material["url"],
                                               scope=material["material_scope"], quarantined=material["quarantined"])
                                          for article_id, material in state["retrieved"].items()]
        record["what_changed"] = state["results"].get("compare_previous_briefings")
        related = state["results"].get("inspect_related_stories", {}).get("related", [])
        retrieved_ids = {article_id for article_id, material in state["retrieved"].items()
                         if article_id in target_ids and not material["quarantined"]}
        corroboration = sum(any(evidence_id(url) in retrieved_ids for url in group["evidence_urls"])
                            for group in candidate["reporting_groups"])
        assessment = availability_assessment(state['retrieved'], set(target_ids), candidate['reporting_groups'])
        disagreements = assessment['disagreements']
        record['corrections'] = assessment['corrections']
        if record['summary'] and assessment['replacements']:
            revised = []
            for citation in record['summary']['citations']:
                replacement = assessment['replacements'].get(citation['evidence_id'])
                if replacement:
                    material = state['retrieved'][replacement]['material']
                    claim = dict(evidence_id=replacement, quote=choose_source_excerpt(material))
                else:
                    claim = dict(evidence_id=citation['evidence_id'], quote=citation['quote'])
                if claim not in revised: revised.append(claim)
            record['summary'] = supported_summary(revised,state['retrieved'],set(target_ids))
            for old_id,new_id in assessment['replacements'].items():
                if clean_text(candidate['title']) == clean_text(tools.articles[old_id]['title']):
                    record['corrected_title'] = short_text(tools.articles[new_id]['title'],200)
        undated = sorted(article_id for article_id in retrieved_ids
                         if publication_time(state['retrieved'][article_id].get('published_at')) is None)
        record['publication_dates'] = dict(status='incomplete' if undated else 'complete',
                                          missing_evidence_ids=undated)
        record['conflicts'] = dict(status='detected' if disagreements else 'not_detected',
                                   scope='Explicit player availability for the same named player and game day only',
                                   date_coverage='incomplete' if undated else 'complete',
                                   items=disagreements)
        record["corroboration"] = dict(retrieved_reporting_groups=corroboration,
                                       status='conflicting_reports' if disagreements else
                                       'timing_unverified' if undated else
                                       "multiple_reporting_groups" if corroboration >= 2 else "limited")
        record["uncertainty"] = ["Only saved article excerpts were checked, not the full articles."]
        if undated:
            record['uncertainty'].append('Some sources have no usable publication date. Their use of “today,” “tonight” or “tomorrow” cannot be tied to a game date.')
        if assessment['corrections']:
            record['uncertainty'].append('A source corrected its earlier availability report. The summary uses the correction; the earlier source remains in the evidence history.')
        if disagreements:
            record['uncertainty'].append('Sources disagree on player availability. The final status is still unconfirmed.')
        if corroboration < 2:
            record["uncertainty"].append("Independent corroboration is limited.")
        if candidate.get("has_rumor"):
            record["uncertainty"].append("Rumor wording appears in this story. Treat the claim as unconfirmed.")
        if related:
            record["uncertainty"].append("Related coverage shares names or teams and may describe a different event.")
        record["decision_summary"] = f"Read {len(state['retrieved'])} excerpts. Estimated reporting groups: {corroboration}. Story scores are unchanged."
        if record["summary"] and candidate.get("has_rumor"):
            record["summary"]["text"] = "Unconfirmed report. " + record["summary"]["text"]
    for candidate in briefing["candidates"] + briefing["stories"]:
        candidate["investigation"] = records[candidate["id"]]
        if records[candidate['id']].get('corrected_title'):
            candidate['title'] = records[candidate['id']]['corrected_title']
        candidate["summary"] = records[candidate["id"]]["summary"]
        candidate["what_changed"] = records[candidate["id"]]["what_changed"]
    briefing["investigation"] = dict(provider=provider.name, status="complete" if all(record["status"] in {"complete", "insufficient_evidence"} for record in records.values()) else "partial",
                                     limits=asdict(settings), steps_used=steps,
                                     llm_calls=0,
                                     elapsed_seconds=max(0, clock() - started_at))
    return briefing

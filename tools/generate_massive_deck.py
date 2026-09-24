"""Generate data/events_deck.json: the full CLASSIFIED DILEMMA deck.

    python tools/generate_massive_deck.py

The cards already in the deck (the original set and the Expansion 1.1 cards) are kept; the cards below are added
or replaced by id, so re-running is safe. Categories are normalised to DOMESTIC, MILITARY, DIPLOMACY, ESPIONAGE
and CRISIS (plus ECONOMY and RESEARCH for the older cards). The result is validated before it is written: unique
ids, 2-3 choices each, known effect keys, and every event-chain follow-up pointing at a card that exists.

EVENT CHAINS use the same schema as email follow-ups, with `card` in place of `email`:
    "follow_ups": [{"card": "<card id>", "delay_weeks": 2}]
    "follow_ups": [{"delay_weeks": 3, "chance": 0.7,
                    "outcomes": [{"card": "<id>", "weight": 60}, {"card": "<id>", "weight": 40}]}]
The second act of a chain is marked "chain_only": true, so it is never drawn at random.

WRITER PACKS: every tools/writer_packs/*.json (a list of cards in the Head Writer's schema) is translated into the
engine schema and merged last, so writer cards replace placeholders with the same id. Writer schema:
    description -> text              condition / conditions -> conditions   (tax_policy_condition -> tax_policies)
    is_chain_only -> chain_only      follow_up {event_id, delay_weeks} (or a list) -> follow_ups [{card, delay_weeks}]
    choices without ids get option_1..3; a chain-only card may have a single choice (an acknowledgement)
    effects: civil_morale -> morale; change_tax_policy "High" -> tax_policy "high";
             grant_technology "<tech name or id>" -> tech; start_outbreak "<disease name>" -> outbreak {disease, city};
             trigger_disaster "<disaster name>" -> disaster {kind, region}. A city / region the effect does not name
             is taken from the first Kestrian city named in the card's text (or in the text of the card that leads to
             it; with no place named, the engine picks one). Everything else uses the engine keys
             (src/engine/effects.py). Unknown keys stop the build.
RETIRED lists placeholder cards that a writer card has superseded; they are removed from the deck.

The CARDS below are the programmer's placeholder drafts: the Head Writer's packs replace them over time.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECK = ROOT / "data" / "events_deck.json"
PACKS = ROOT / "tools" / "writer_packs"

CATEGORY_MAP = {"FOREIGN": "DIPLOMACY", "INTELLIGENCE": "ESPIONAGE"}
# placeholder card -> the writer card that superseded it
RETIRED = {"vosk_engineer": "gemini_vosk_defector", "defector_designs": "gemini_vosk_defector_success",
           "defector_died": "gemini_vosk_defector_dead"}


def card(id_, title, category, text, choices, weight=5, conditions=None, chain_only=False):
    entry = {"id": id_, "title": title, "category": category, "weight": weight, "text": text, "choices": choices}
    if conditions:
        entry["conditions"] = conditions
    if chain_only:
        entry["chain_only"] = True
    return entry


def choice(id_, label, hint, effects=None, follow_ups=None):
    entry = {"id": id_, "label": label, "hint": hint, "effects": effects or {}}
    if follow_ups:
        entry["follow_ups"] = follow_ups
    return entry


def then(card_id, weeks):
    return {"card": card_id, "delay_weeks": weeks}


def maybe(weeks, chance, *outcomes):
    return {"delay_weeks": weeks, "chance": chance,
            "outcomes": [{"card": c, "weight": w} for c, w in outcomes]}


CARDS = [
    # ================================================================== DOMESTIC
    card("munitions_quota", "The Kessel Works Quota", "DOMESTIC",
         "The Ministry of Munitions has raised the Kessel Works quota by a third. Foremen report women fainting at the "
         "shell presses after fourteen-hour shifts; the TNT dust has turned their skin yellow. The works director asks "
         "for the Lord Protector's decision: hold the quota, or let the lines slow.",
         [choice("enforce", "Hold the quota", "Every shell counts. The women are paid for their sacrifice.",
                 {"modifier": {"key": "factory_efficiency", "value": 0.15, "weeks": 3}, "morale": -3,
                  "city_morale": {"Kessel Works": -15}},
                 [maybe(3, 0.6, ("kessel_explosion", 1))]),
          choice("relent", "Cut the shifts to ten hours", "The front will wait a little longer for its shells.",
                 {"modifier": {"key": "factory_efficiency", "value": -0.08, "weeks": 3}, "morale": 2,
                  "city_morale": {"Kessel Works": 10}}),
          choice("convicts", "Send in convict labour", "Internal Security has prisoners to spare.",
                 {"morale": -2, "modifier": {"key": "factory_efficiency", "value": 0.05, "weeks": 4}},
                 [maybe(2, 0.5, ("convict_sabotage", 1))])],
         weight=7),
    card("kessel_explosion", "Explosion at the Kessel Works", "CRISIS",
         "At 03:10 the filling shed of Line Four went up. Windows broke in Ironvale, eleven miles away. Rescue teams are "
         "still pulling women from the rubble; the dead number in the hundreds. The survivors say the line was running "
         "too fast for anyone to follow the safety drill.",
         [choice("saboteurs", "Blame Vosk saboteurs", "Internal Security will find someone to hang.",
                 {"morale": -2, "military_morale": 1, "ai_tension": 3}),
          choice("compensate", "Compensate the families", "Pensions for the widowers, a memorial in the square.",
                 {"treasury": -40000, "morale": 2, "city_morale": {"Kessel Works": 10}}),
          choice("silence", "Suppress the news", "The censors will call it an industrial accident.",
                 {"morale": -1, "city_morale": {"Kessel Works": -20}})],
         chain_only=True),
    card("convict_sabotage", "Sabotage on the Filling Lines", "ESPIONAGE",
         "Fuse caps filled with sand, detonators wired backwards: the convict gangs at the Kessel Works have been quietly "
         "ruining a tenth of what they touch. The gunners at the front have noticed.",
         [choice("hang", "Hang the ringleaders", "An example for the others.",
                 {"morale": -2, "military_morale": 2}),
          choice("withdraw", "Send them back to the camps", "Lose the labour; keep the shells good.",
                 {"modifier": {"key": "factory_efficiency", "value": -0.05, "weeks": 2}, "army_morale": 1})],
         chain_only=True),
    card("black_market", "The Black Market", "DOMESTIC",
         "Butter at forty times the ration price, petrol in whisky bottles, army boots in the flea markets: the black "
         "market has taken over the back streets of Aldmark. The Director of Internal Security wants authority to shoot "
         "profiteers on sight.",
         [choice("shoot", "Shoot the profiteers", "Public executions on Canal Street.",
                 {"morale": -3, "stockpiles": {"rations": 2000}}),
          choice("license", "License small traders", "Tax what cannot be stopped.",
                 {"treasury": 20000, "morale": 1}),
          choice("ignore", "Look the other way", "It keeps the city fed, after a fashion.",
                 {"morale": 1}, [maybe(4, 0.7, ("black_market_cartel", 1))])],
         weight=6),
    card("black_market_cartel", "The Rail-Yard Cartel", "ESPIONAGE",
         "Left alone, the black market has organised. A cartel of railwaymen and quartermasters now skims one wagon in "
         "twenty from the military trains out of Aldmark yards, and the fuel dumps at the front are running dry.",
         [choice("raid", "Raid the yards", "Military police, a night, and no warrants.",
                 {"treasury": 60000, "military_morale": -1, "equipment": {"fuel_drums": 1500}}),
          choice("coopt", "Buy them into the war effort", "Their networks can move what the army cannot.",
                 {"treasury": -30000, "equipment": {"fuel_drums": 2500}, "morale": -1})],
         chain_only=True),
    card("refugee_columns", "Refugees on the Greywater Road", "DOMESTIC",
         "Tens of thousands of refugees from the Eastmarch are streaming into Greywater with carts, cattle and "
         "nothing else. The town council demands a hospital, soup kitchens and somewhere to put them before the "
         "winter kills them in the fields.",
         [choice("hospital", "Build a hospital and shelters", "The state pays; Greywater will remember.",
                 {"treasury": -60000, "build": {"city": "Greywater", "building": "hospital"},
                  "city_morale": {"Greywater": 12}, "morale": 1}),
          choice("camps", "Put up tent camps", "Cheap, fast, and crowded.",
                 {"treasury": -15000, "morale": -1}, [maybe(3, 0.5, ("refugee_typhus", 1))]),
          choice("turn_back", "Turn them back east", "Keep the roads clear for the army.",
                 {"morale": -5, "military_morale": 1, "city_morale": {"Greywater": 5}})],
         weight=6),
    card("refugee_typhus", "Typhus in the Camps", "CRISIS",
         "The tent camps outside Greywater are full of lice, and the lice are full of typhus. The camp doctors have "
         "run out of everything.",
         [choice("delouse", "Delousing stations and a quarantine line", "Expensive, and it may be too late.",
                 {"treasury": -30000, "morale": 1}),
          choice("burn", "Burn the camps and scatter the refugees", "The fever goes with them.",
                 {"morale": -4, "outbreak": {"disease": "trench_typhus", "city": "Greywater"}})],
         chain_only=True),
    card("church_bells", "The Bells of Aldmark", "DOMESTIC",
         "The Ministry of Munitions wants the bronze bells of every church in the capital melted for shell casings. "
         "The Bishop of Aldmark has announced that he will chain himself to the cathedral tower.",
         [choice("seize", "Seize the bells", "Bronze is bronze.",
                 {"stockpiles": {"steel": 800}, "morale": -3}),
          choice("spare", "Spare the bells", "Some things are worth more than shells.", {"morale": 2}),
          choice("compromise", "Take only the small bells", "The cathedral keeps its great bell.",
                 {"stockpiles": {"steel": 300}, "morale": -1})]),
    card("student_protest", "The University Occupation", "DOMESTIC",
         "Students have occupied the Aldmark University great hall with banners reading PEACE NOW and BRING THEM HOME. "
         "Internal Security has surrounded the building and awaits orders.",
         [choice("storm", "Send in Internal Security", "Clear the hall before it spreads.",
                 {"morale": -3, "military_morale": 1}, [then("martyr_funeral", 2)]),
          choice("negotiate", "Negotiate", "Hear them out, promise nothing.", {"morale": 1, "military_morale": -1}),
          choice("draft", "Draft the ringleaders", "Let them see the trenches for themselves.",
                 {"manpower": 500, "morale": -2})],
         weight=6),
    card("martyr_funeral", "A Student's Funeral", "DOMESTIC",
         "A nineteen-year-old medical student died of his injuries after the university was cleared. His funeral "
         "procession is expected to draw fifty thousand people.",
         [choice("ban", "Ban the procession", "Internal Security will hold the bridges.", {"morale": -4}),
          choice("allow", "Allow it under guard", "Let them grieve, and watch them.",
                 {"morale": -1, "city_morale": {"Aldmark": 4}}),
          choice("attend", "The Lord Protector walks behind the coffin", "A gesture the generals will not forgive.",
                 {"morale": 4, "military_morale": -3})],
         chain_only=True),
    card("coal_crisis", "No Coal for the Cities", "DOMESTIC",
         "The frost has come early and the coal ration has failed in half the cities of the Commonwealth. Families "
         "are burning floorboards. The Ministry of Fuel can keep either the homes or the steelworks warm, not both.",
         [choice("homes", "Heat the homes", "The steelworks bank their furnaces.",
                 {"modifier": {"key": "factory_efficiency", "value": -0.1, "weeks": 3}, "morale": 3}),
          choice("works", "Keep the steelworks burning", "The people must endure.", {"morale": -4}),
          choice("tor_coal", "Buy coal from Tor", "At Tor's prices, and Tor's gratitude.",
                 {"treasury": -45000, "stockpiles": {"coal": 6000}, "relations": {"tor": 3}})],
         conditions={"seasons": ["Winter", "Autumn"]}, weight=7),
    card("iron_line_film", "The Iron Line", "DOMESTIC",
         "The Ministry of Information proposes a great patriotic film: 'The Iron Line', three hours of heroism in the "
         "Frontier trenches, with real soldiers as extras and real shells for the barrage scenes.",
         [choice("fund", "Fund the film", "Art is a weapon.",
                 {"treasury": -30000, "morale": 1}, [maybe(4, 1.0, ("film_triumph", 65), ("film_flop", 35))]),
          choice("refuse", "Refuse", "The shells are needed at the front.", {"military_morale": 1})]),
    card("film_triumph", "'The Iron Line' Premieres", "DOMESTIC",
         "Audiences weep and cheer. Queues stretch around the cinemas of every city. The Oakhaven distributors want "
         "the film for the Republic.",
         [choice("export", "Export it to Oakhaven", "Let the Republic see what Kestria is fighting for.",
                 {"relations": {"oakhaven": 5}, "treasury": 10000, "morale": 2}),
          choice("home", "Keep it for the home front", "Screen it in every barracks too.",
                 {"morale": 3, "army_morale": 2})],
         chain_only=True),
    card("film_flop", "'The Iron Line' Is Laughed Off the Screen", "DOMESTIC",
         "The premiere audience howled at the cardboard tanks and the actor-general's wooden speeches. Veterans walked "
         "out. The foreign press is having a wonderful time.",
         [choice("blame", "Blame the director", "He will make training films in Harrowfen.", {"morale": -1}),
          choice("pull", "Pull it quietly", "Every print into the furnace.", {"treasury": -5000})],
         chain_only=True),
    card("war_widows", "The War Widows' Pensions", "DOMESTIC",
         "The Treasury is eleven weeks behind on war widows' pensions. Three thousand black-clad women are standing "
         "in silence outside the Ministry of Finance.",
         [choice("pay", "Pay them in full", "The state keeps its promises.", {"treasury": -50000, "morale": 3}),
          choice("delay", "Delay payment", "The war comes first.", {"morale": -3, "military_morale": -2}),
          choice("half", "Pay half now", "Something is better than nothing.", {"treasury": -25000, "morale": 1})],
         conditions={"min_turn": 10}),
    card("harvest_requisition", "Requisitioning the Harvest", "DOMESTIC",
         "The army quartermasters want the whole Harrowfen harvest requisitioned at fixed prices. The farmers say "
         "they will burn their ricks first.",
         [choice("requisition", "Requisition it", "Soldiers with bayonets at every threshing floor.",
                 {"stockpiles": {"rations": 3000}, "morale": -4}),
          choice("buy", "Buy at market price", "Fair, and ruinous.", {"treasury": -40000, "stockpiles": {"rations": 3000}}),
          choice("leave", "Leave the farmers alone", "The army will tighten its belt.", {"military_morale": -2})],
         conditions={"seasons": ["Autumn", "Summer"]}),
    card("women_factories", "Women to the Factories", "DOMESTIC",
         "The Ministry of Labour proposes to conscript unmarried women into the munitions industry. The Church, the "
         "unions and half the Assembly are outraged; the other half say it is long overdue.",
         [choice("conscript", "Conscript them", "Industry needs hands.",
                 {"modifier": {"key": "factory_efficiency", "value": 0.08}, "morale": -3}),
          choice("volunteers", "Call for volunteers", "Posters, pay and pride.",
                 {"modifier": {"key": "factory_efficiency", "value": 0.03}, "morale": 1}),
          choice("refuse", "Refuse", "Kestrian women will not be drafted.", {"morale": 1})],
         conditions={"min_turn": 8}),
    card("refugee_hospital_petition", "The Hospital Petition", "DOMESTIC",
         "A petition with two hundred thousand signatures demands a modern hospital for Kessel Works, where the "
         "munitions workers are dying of TNT poisoning in the old infirmary.",
         [choice("build", "Build the hospital", "The state pays; construction starts at once.",
                 {"treasury": -60000, "build": {"city": "Kessel Works", "building": "hospital"},
                  "city_morale": {"Kessel Works": 15}}),
          choice("refuse", "The war comes first", "No money for bricks and beds.",
                 {"morale": -2, "city_morale": {"Kessel Works": -12}})],
         conditions={"requires_city_without": {"city": "Kessel Works", "building": "hospital"}, "min_turn": 6}),

    # ================================================================== MILITARY
    card("draft_dodgers", "Draft Dodgers in the Harrowfen", "MILITARY",
         "Three thousand young men are hiding from the call-up in the Harrowfen marshes, fed by their families and "
         "warned by the village priests. The Adjutant-General wants a manhunt.",
         [choice("manhunt", "Order the manhunt", "Dogs, patrols and hostages.",
                 {"manpower": 3000, "morale": -3, "military_morale": 1}),
          choice("amnesty", "Offer an amnesty", "Come in now and nothing will be said.",
                 {"manpower": 1500, "morale": 1}),
          choice("ignore", "Leave them in the marsh", "The army is not short of men — yet.", {"military_morale": -2})]),
    card("shell_scandal", "The Dud Shell Scandal", "MILITARY",
         "Artillery officers report that one shell in five from the latest Kessel Works lots fails to explode. "
         "Infantry went over the top last month behind a barrage that never came.",
         [choice("court_martial", "Court-martial the inspectors", "Heads will roll at the Ministry of Munitions.",
                 {"military_morale": 2, "modifier": {"key": "factory_efficiency", "value": -0.05, "weeks": 2}}),
          choice("cover", "Cover it up", "Nothing must shake faith in Kestrian industry.",
                 {"military_morale": -3}, [maybe(3, 0.7, ("shell_scandal_press", 1))]),
          choice("tor_shells", "Buy shells from Tor instead", "Toran shells are dear, but they go off.",
                 {"treasury": -25000, "equipment": {"shell_152_he": 3000}, "relations": {"tor": 2}})],
         weight=6),
    card("shell_scandal_press", "THE HERALD: 'DUD SHELLS KILLED OUR SONS'", "DOMESTIC",
         "The Aldmark Herald has the whole story, with a photograph of a crate of dud shells and the inspector's "
         "signature on the lot card. The Assembly is in uproar.",
         [choice("resign", "Sack the Minister of Munitions", "A sacrifice to the public.",
                 {"morale": 2, "military_morale": -1}),
          choice("censor", "Seize the Herald's presses", "The story dies tonight.", {"morale": -4, "military_morale": 1})],
         chain_only=True),
    card("great_push", "High Command Proposes the Great Push", "MILITARY",
         "The General Staff has drawn up plans for a general offensive along the whole Frontier: three days of "
         "barrage, then every division over the top at once. They promise to be in Karzan by the harvest.",
         [choice("approve", "Approve the plan", "The generals are delighted; the gunners start stacking shells.",
                 {"military_morale": 5, "army_morale": 3, "ai_tension": 6}),
          choice("refuse", "Refuse", "Kestria will not bleed itself white.", {"military_morale": -4}),
          choice("study", "Order a further study", "Buy time without saying no.", {"military_morale": -1})],
         conditions={"min_turn": 6}),
    card("christmas_truce", "Truce in No-Man's-Land", "MILITARY",
         "On the night of the winter solstice the Vosk trenches began to sing. By morning men of both armies were "
         "exchanging cigarettes and photographs in no-man's-land.",
         [choice("punish", "Court-martial the officers", "Fraternisation is treason.",
                 {"army_morale": -3, "military_morale": 2}),
          choice("allow", "Look the other way", "One day's peace will not lose the war.",
                 {"army_morale": 3, "ai_tension": -5, "military_morale": -2}),
          choice("exploit", "Send intelligence officers into the truce", "Every Vosk soldier knows something.",
                 {"flags": {"network_lantern": True}, "ai_tension": 3, "army_morale": -1})],
         conditions={"seasons": ["Winter"]}),
    card("deserters", "The Clemency Petition", "MILITARY",
         "Six men of the 3rd Division, sentenced to death for desertion under fire, are to be shot at dawn. Their "
         "mothers have walked from Stanhope to beg for their lives.",
         [choice("execute", "Let the sentences stand", "Discipline is the army.", {"military_morale": 2, "morale": -3}),
          choice("penal", "Commute to a penal battalion", "They can die usefully.", {"manpower": 300, "morale": 1}),
          choice("pardon", "Pardon them", "Mercy from the Lord Protector.", {"military_morale": -4, "morale": 2})]),
    card("veterans_march", "The March of the Maimed", "MILITARY",
         "Ten thousand disabled veterans — men on crutches, men with no faces, men pushed in barrows — are marching "
         "on Aldmark to demand the pensions they were promised.",
         [choice("grant", "Grant the pensions", "The nation pays its debts.", {"treasury": -35000, "morale": 3,
                                                                             "military_morale": 2}),
          choice("disperse", "Disperse the march", "Mounted police on the bridges.", {"morale": -4, "military_morale": -3})],
         conditions={"min_turn": 12}),
    card("airfield_fire", "Fire at Harrow Airfield", "CRISIS",
         "The fuel dump at Harrow airfield exploded last night, taking a hangar and a dozen fighters with it. The base "
         "commander says it was an accident; the military police are not so sure.",
         [choice("investigate", "Open an investigation", "Find out who, or what.", {"equipment": {"strike_aircraft": -4}},
                 [maybe(2, 1.0, ("airfield_saboteur", 50), ("airfield_negligence", 50))]),
          choice("rebuild", "Rebuild and move on", "No time for inquiries.",
                 {"treasury": -30000, "equipment": {"strike_aircraft": -4}})]),
    card("airfield_saboteur", "The Harrow Saboteur", "ESPIONAGE",
         "The investigation has found him: a fuel clerk with a Vosk grandmother, a bank account in Vael and a "
         "short-wave set in his attic.",
         [choice("hang", "Hang him publicly", "A warning to every traitor.", {"morale": 2, "ai_tension": 3}),
          choice("turn", "Turn him", "He will send Karzan what we want them to hear.",
                 {"flags": {"network_lantern": True}, "ai_tension": -3})],
         chain_only=True),
    card("airfield_negligence", "Negligence at Harrow", "MILITARY",
         "No saboteur: a drunk mechanic, a cigarette, and a fuel dump that should never have been sited next to "
         "the hangars.",
         [choice("court_martial", "Court-martial the base commander", "Someone must answer.", {"military_morale": -1}),
          choice("quiet", "Keep it quiet", "Sabotage sounds better in the newspapers.", {"morale": -1})],
         chain_only=True),

    # ================================================================== DIPLOMACY
    card("oakhaven_tax_embargo", "Oakhaven Threatens an Embargo", "DIPLOMACY",
         "President Caul's government has sent a stiff note: the Republic cannot continue to trade with a state that "
         "taxes its citizens into penury. Unless Kestria lowers its taxes, Oakhaven will suspend the trade agreement "
         "and every lend-lease licence.",
         [choice("lower", "Lower taxes to Normal", "Keep the Republic sweet.",
                 {"tax_policy": "normal", "relations": {"oakhaven": 8}}),
          choice("refuse", "Refuse the interference", "Kestria sets its own taxes.", {"relations": {"oakhaven": -20},
                                                                                     "morale": 1}),
          choice("promise", "Promise reforms", "Words cost nothing — for now.", {"relations": {"oakhaven": 2}},
                 [then("oakhaven_inspection", 4)])],
         conditions={"tax_policies": ["high", "oppressive"], "requires_trade": "oakhaven"}, weight=8),
    card("oakhaven_inspection", "Oakhaven Observers Arrive", "DIPLOMACY",
         "A delegation from the Oakhaven Senate has arrived to see the promised tax reforms for itself.",
         [choice("truth", "Show them the truth", "Honesty, whatever it costs.", {"relations": {"oakhaven": -5}}),
          choice("potemkin", "Stage a model town", "Fresh paint, full shops, happy workers for a day.",
                 {"treasury": -15000, "relations": {"oakhaven": 6}}),
          choice("lower_now", "Lower taxes before they arrive", "Keep the promise after all.",
                 {"tax_policy": "normal", "relations": {"oakhaven": 10}})],
         chain_only=True),
    card("tor_artillery_offer", "Tor Offers Cheap Artillery", "DIPLOMACY",
         "The Protector-General of Tor offers twenty-four new howitzers at half price — if Kestria formally recognises "
         "Tor's annexation of the Marish coast. Oakhaven will call it a betrayal of every principle Kestria claims.",
         [choice("recognise", "Buy and recognise the annexation", "Guns now, principles later.",
                 {"treasury": -40000, "equipment": {"howitzer_152": 24, "shell_152_he": 2000},
                  "relations": {"tor": 12, "oakhaven": -10}}),
          choice("full_price", "Offer full price without recognition", "Tor may take the money.",
                 {"treasury": -85000, "equipment": {"howitzer_152": 24}, "relations": {"tor": 2}}),
          choice("decline", "Decline", "Kestria does not trade in other nations' land.", {"relations": {"oakhaven": 4, "tor": -4}})],
         conditions={"min_turn": 6}, weight=6),
    card("vael_peace_conference", "Vael Offers Good Offices", "DIPLOMACY",
         "The Council of Savants of Vael offers to host talks between Kestria and the Hegemony in the neutral city of "
         "Aurel: prisoner exchanges first, and perhaps more.",
         [choice("attend", "Send a delegation", "Talking is not surrender.",
                 {"relations": {"vael": 8}, "ai_tension": -8, "military_morale": -2},
                 [maybe(3, 1.0, ("vael_talks_progress", 55), ("vael_talks_collapse", 45))]),
          choice("refuse", "Refuse", "There is nothing to discuss with Karzan.", {"relations": {"vael": -5}, "morale": 1})],
         conditions={"min_turn": 10}),
    card("vael_talks_progress", "Progress at Aurel", "DIPLOMACY",
         "The Vosk delegation has agreed to a general exchange of prisoners, and hints that more could follow.",
         [choice("exchange", "Sign the prisoner exchange", "Thousands of men come home.",
                 {"manpower": 3000, "morale": 3, "ai_tension": -5}),
          choice("walk_away", "Walk away", "An exchange frees their veterans too.", {"relations": {"vael": -5},
                                                                                   "military_morale": 2})],
         chain_only=True),
    card("vael_talks_collapse", "The Aurel Talks Collapse", "DIPLOMACY",
         "The Vosk delegation walked out after one day, calling Kestria's terms 'an insult to the Hegemony'.",
         [choice("blame", "Blame Karzan publicly", "Let the world see who wants peace.",
                 {"relations": {"vael": 4, "oakhaven": 2}, "ai_tension": 4}),
          choice("silence", "Say nothing", "Leave the door open.", {"relations": {"vael": 2}})],
         chain_only=True),
    card("tor_state_visit", "The Protector-General's Visit", "DIPLOMACY",
         "Protector-General Oskar Tor will pass through Port Cassel on his way home from Oakhaven. His ministers hint "
         "that he expects a state reception worthy of a great power — and a gift.",
         [choice("lavish", "A lavish reception", "Guards of honour, a banquet, a gold-mounted sabre.",
                 {"treasury": -40000, "relations": {"tor": 12}}),
          choice("modest", "A modest welcome", "Correct, and cheap.", {"relations": {"tor": 3}}),
          choice("snub", "Snub him", "Kestria does not grovel to dictators.", {"relations": {"tor": -10}, "morale": 2})]),
    card("oakhaven_war_loan", "The Oakhaven War Loan", "DIPLOMACY",
         "A consortium of Oakhaven banks offers Kestria a war loan of a quarter of a million, repayable with interest "
         "in eight weeks. The Treasury is tempted; the Finance Minister is terrified.",
         [choice("accept", "Take the loan", "Money now; the reckoning later.",
                 {"treasury": 250000}, [then("loan_due", 8)]),
          choice("decline", "Decline", "Kestria will not mortgage its future.", {})],
         conditions={"requires_relation": {"oakhaven": 10}}),
    card("loan_due", "The Oakhaven Loan Falls Due", "DIPLOMACY",
         "The Oakhaven consortium presents its bill: three hundred thousand, principal and interest, payable this week.",
         [choice("repay", "Repay in full", "Kestria pays its debts.", {"treasury": -300000, "relations": {"oakhaven": 5}}),
          choice("default", "Default", "Let them sue.", {"relations": {"oakhaven": -30}, "morale": -2}),
          choice("refinance", "Refinance for another eight weeks", "Pay the interest, roll the rest.",
                 {"treasury": -60000, "relations": {"oakhaven": -3}}, [then("loan_due", 8)])],
         chain_only=True),
    card("oakhaven_journalist", "An Oakhaven Journalist at the Front", "DIPLOMACY",
         "Clara Whitlow of the Oakhaven Evening Standard, the most famous war correspondent in the Republic, asks for "
         "a pass to the Frontier trenches.",
         [choice("grant", "Grant the pass", "Let the Republic see the war as it is.", {"relations": {"oakhaven": 4}},
                 [maybe(3, 1.0, ("journalist_praise", 60), ("journalist_exposé", 40))]),
          choice("deny", "Deny it", "No foreigners at the front.", {"relations": {"oakhaven": -3}})]),
    card("journalist_praise", "'THE LIONS OF THE FRONTIER'", "DIPLOMACY",
         "Whitlow's dispatches — mud, courage, and quiet Kestrian decency — are front-page news in Oakhaven.",
         [choice("syndicate", "Syndicate it everywhere", "Every neutral paper should print it.",
                 {"relations": {"oakhaven": 8, "vael": 3}, "morale": 2}),
          choice("thank", "Thank her quietly", "Modesty plays well abroad.", {"relations": {"oakhaven": 4}})],
         chain_only=True),
    card("journalist_exposé", "'THE BUTCHERS OF THE FRONTIER'", "DIPLOMACY",
         "Whitlow has written about the field punishments, the dud shells and the penal battalions. The Oakhaven "
         "Senate wants hearings.",
         [choice("expel", "Expel her", "She abused Kestrian hospitality.", {"relations": {"oakhaven": -10}, "morale": 1}),
          choice("reform", "Promise reforms at the front", "Fix what she found.",
                 {"treasury": -30000, "army_morale": 3, "relations": {"oakhaven": 4}})],
         chain_only=True),
    card("vael_scientists", "Refugee Scientists from Vael", "DIPLOMACY",
         "A group of Vaelish chemists, quarrelling with their own Council, ask for asylum and laboratories in Kestria. "
         "The Council of Savants would regard it as theft.",
         [choice("welcome", "Welcome them", "Their knowledge is ours.", {"research_weeks": 5, "relations": {"vael": -12}}),
          choice("return", "Send them home", "Vael's friendship is worth more.", {"relations": {"vael": 8}})],
         conditions={"requires_research": True}),

    # ================================================================== ESPIONAGE
    card("agent_in_karzan", "Our Man in Karzan", "ESPIONAGE",
         "Agent LANTERN-7, our best source inside the Vosk General Staff, has sent an unusual request: a large sum of "
         "money and a promise of exfiltration. Counter-intelligence thinks he may have been turned.",
         [choice("trust", "Trust him", "Pay, and keep him in place.", {"treasury": -30000},
                 [maybe(3, 1.0, ("agent_gold", 50), ("agent_betrayed", 50))]),
          choice("burn", "Burn him", "Cut contact. Lose the source.", {"military_morale": -1})]),
    card("agent_gold", "LANTERN-7 Delivers", "ESPIONAGE",
         "LANTERN-7's report is pure gold: the Vosk order of battle, the ammunition stocks of the Western Front, and "
         "the date of their next offensive.",
         [choice("prepare", "Prepare the defences", "Every division stands to.", {"army_morale": 3,
                                                                              "flags": {"network_lantern": True}}),
          choice("share", "Share it with Oakhaven", "Proof that the Hegemony plans aggression.",
                 {"relations": {"oakhaven": 6}, "flags": {"network_lantern": True}})],
         chain_only=True),
    card("agent_betrayed", "LANTERN-7 Was Turned", "ESPIONAGE",
         "Everything LANTERN-7 has sent for two months was written in Karzan. Our dispositions on the northern flank "
         "were built on Vosk fiction.",
         [choice("purge", "Purge the intelligence service", "Start again from nothing.",
                 {"military_morale": -2, "army_morale": -2, "flags": {"network_lantern": False}}),
          choice("feed_back", "Feed them lies in return", "Two can play.", {"ai_tension": -4, "army_morale": -2})],
         chain_only=True),
    card("cipher_breakthrough", "The Codebreakers' Request", "ESPIONAGE",
         "The cryptanalysts at Stanhope say they are close to breaking the Vosk high-command cipher. They need "
         "mathematicians, calculating machines and money.",
         [choice("fund", "Fund the crash programme", "Whatever they need.", {"treasury": -40000},
                 [maybe(3, 0.7, ("cipher_cracked", 1))]),
          choice("later", "Not now", "The front needs shells more than sums.", {})]),
    card("cipher_cracked", "The Vosk Cipher Is Broken", "ESPIONAGE",
         "Stanhope has done it: the Vosk high-command traffic is readable. The question is how to use it without "
         "revealing that we can.",
         [choice("quiet", "Exploit it quietly", "Never act on more than we could have guessed.",
                 {"flags": {"network_lantern": True}, "ai_tension": -3, "army_morale": 2}),
          choice("leak", "Reveal Vosk war plans to the world", "Shame Karzan in every capital.",
                 {"relations": {"oakhaven": 8, "vael": 5}, "ai_tension": 5})],
         chain_only=True),
    card("internal_security_powers", "Emergency Powers for Internal Security", "ESPIONAGE",
         "The Director of Internal Security asks for powers to detain suspects without trial, open any letter and tap "
         "any telephone. 'The Vosk are everywhere,' he says.",
         [choice("grant", "Grant the powers", "Security first.", {"morale": -3, "military_morale": 2,
                                                                 "flags": {"counterintel_purge": True}}),
          choice("refuse", "Refuse", "Kestria is not the Hegemony.", {"morale": 1, "military_morale": -1})]),
    card("assassination_plot", "The Ostrakov Operation", "ESPIONAGE",
         "Our agents propose to assassinate Marshal Ostrakov, commander of the Vosk Western Front, with a bomb in his "
         "staff car. The operation needs money and the Lord Protector's signature.",
         [choice("authorise", "Authorise the operation", "Decapitate the Western Front.", {"treasury": -30000},
                 [maybe(2, 1.0, ("assassination_success", 40), ("assassination_blowback", 60))]),
          choice("veto", "Veto it", "Kestria does not murder generals.", {})],
         conditions={"min_turn": 8}),
    card("assassination_success", "Marshal Ostrakov Is Dead", "ESPIONAGE",
         "The bomb went off under the Marshal's car on the Karzan road. The Western Front staff is in chaos, and "
         "Karzan is howling for revenge.",
         [choice("claim", "Let it be known it was us", "Terror for their generals.",
                 {"military_morale": 4, "ai_tension": 12}),
          choice("deny", "Deny everything", "Partisans, perhaps. Who can say?", {"military_morale": 2, "ai_tension": 6})],
         chain_only=True),
    card("assassination_blowback", "The Agents Are Taken", "ESPIONAGE",
         "The bomb team was arrested at a roadblock. Radio Karzan is broadcasting their confessions, and Vael has "
         "recalled its ambassador for consultations.",
         [choice("disown", "Disown them", "They were adventurers.", {"relations": {"vael": -6}, "ai_tension": 10}),
          choice("trade", "Offer a prisoner trade", "Get our men back.", {"relations": {"vael": -3}, "ai_tension": 6,
                                                                         "manpower": -200})],
         chain_only=True),
    card("lost_briefcase", "The Lost Briefcase", "ESPIONAGE",
         "A staff major left his briefcase on the Aldmark–Kestrel Cross express. It held the deployment plan for the "
         "whole southern flank. The briefcase has not been found.",
         [choice("redraw", "Redraw every plan", "Weeks of work, but safe.", {"army_morale": -2, "military_morale": -1}),
          choice("assume", "Assume it went into a ditch", "Nobody could have found it.", {},
                 [maybe(2, 0.5, ("plans_exploited", 1))])]),
    card("plans_exploited", "The Vosk Knew", "MILITARY",
         "The Vosk attack came exactly where the lost plans said our line was thinnest. Our divisions were caught "
         "reshuffling.",
         [choice("purge_staff", "Purge the General Staff", "Heads for every stolen page.",
                 {"military_morale": -3, "army_morale": -2}),
          choice("bad_luck", "Call it bad luck", "Nothing can be proved.", {"army_morale": -4})],
         chain_only=True),
    card("radio_karzan", "Radio Karzan", "ESPIONAGE",
         "Every night Radio Karzan broadcasts the names of Kestrian prisoners, dance music and a honeyed voice "
         "asking Kestrian soldiers why they are dying for the Lord Protector.",
         [choice("jam", "Jam the broadcasts", "Transmitters on every hill.", {"treasury": -20000, "morale": 1}),
          choice("answer", "Answer with Radio Aldmark", "Our own voice, our own music.", {"treasury": -10000, "morale": 2}),
          choice("ignore", "Ignore it", "Nobody believes enemy propaganda.", {"morale": -2, "army_morale": -1})]),

    # ================================================================== CRISIS
    card("greywater_dam", "Cracks in the Greywater Dam", "CRISIS",
         "Engineers report cracks spreading across the face of the great Greywater dam, weakened by the winter frosts. "
         "If it goes, the valley floods from the reservoir to the sea.",
         [choice("repair", "Emergency repairs", "Every engineer and every sack of cement.", {"treasury": -45000}),
          choice("evacuate", "Evacuate the valley", "Save the people, abandon the farms.",
                 {"morale": -2, "city_morale": {"Greywater": -10}, "stockpiles": {"grain": -3000}}),
          choice("hope", "Hope it holds", "The engineers are always pessimists.", {},
                 [maybe(3, 0.45, ("dam_burst", 1))])],
         conditions={"seasons": ["Winter", "Spring"]}),
    card("dam_burst", "THE GREYWATER DAM HAS BURST", "CRISIS",
         "At dawn the Greywater dam gave way. A wall of water is roaring down the valley toward the sea.",
         [choice("lead", "The Lord Protector goes to the valley", "Be seen among the survivors.",
                 {"morale": 1, "disaster": {"kind": "severe_flooding", "region": "greywater"}}),
          choice("blame", "Blame the engineers", "Somebody should have warned us.",
                 {"morale": -2, "disaster": {"kind": "severe_flooding", "region": "greywater"}})],
         chain_only=True),
    card("barracks_flu", "Influenza in the Training Depots", "CRISIS",
         "A virulent influenza is sweeping the recruit depots around Aldmark. Recruits are dying before they ever see "
         "a trench.",
         [choice("quarantine", "Close the depots", "No new men for a month.",
                 {"manpower": -3000, "treasury": -20000}),
          choice("carry_on", "Keep training", "The front needs every man.", {},
                 [maybe(2, 0.7, ("barracks_flu_spreads", 1))])]),
    card("barracks_flu_spreads", "The Depot Fever Reaches the City", "CRISIS",
         "The influenza has escaped the barracks: the Aldmark tenements are full of it.",
         [choice("ministry", "Put the Ministry of Health in charge", "Doctors, not generals.",
                 {"treasury": -15000, "outbreak": {"disease": "industrial_flu", "city": "Aldmark"}}),
          choice("army", "Put the army in charge", "Cordons and curfews.",
                 {"military_morale": -1, "outbreak": {"disease": "industrial_flu", "city": "Aldmark"}})],
         chain_only=True),
    card("ironvale_firedamp", "Firedamp at Ironvale", "CRISIS",
         "The mine inspectors have found firedamp gas building up in the deep galleries of the Ironvale colliery. "
         "Closing the pit means no coal for the steelworks for a month.",
         [choice("close", "Close the pit", "Safety first.", {"modifier": {"key": "factory_efficiency", "value": -0.08,
                                                                          "weeks": 4}}),
          choice("dig", "Keep digging", "Ventilate as best they can.", {},
                 [maybe(2, 0.5, ("ironvale_explosion", 1))])]),
    card("ironvale_explosion", "Explosion in the Ironvale Galleries", "CRISIS",
         "The firedamp has exploded. The main shaft is choked and the colliery railway torn up.",
         [choice("rescue", "Everything for the rescue", "The whole nation holds its breath.",
                 {"treasury": -20000, "morale": 1, "disaster": {"kind": "mine_collapse", "region": "ironvale"}}),
          choice("seal", "Seal the galleries", "The fire must not spread.",
                 {"morale": -3, "disaster": {"kind": "mine_collapse", "region": "ironvale"}})],
         chain_only=True),
    card("aldmark_water", "Something in the Water", "CRISIS",
         "The Aldmark waterworks report a sewage main has burst into the city's filter beds. The chief engineer wants "
         "to chlorinate the whole supply at once, at great cost.",
         [choice("chlorinate", "Chlorinate at once", "Expensive, and safe.", {"treasury": -20000}),
          choice("deny", "Deny there is a problem", "Panic would be worse than the water.", {},
                 [maybe(2, 0.6, ("aldmark_cholera", 1))])]),
    card("aldmark_cholera", "Cholera in the Capital", "CRISIS",
         "The first cases of cholera have appeared in the Aldmark river wards.",
         [choice("admit", "Admit the failure", "The truth, and the doctors.",
                 {"morale": -1, "outbreak": {"disease": "cholera", "city": "Aldmark"}}),
          choice("scapegoat", "Arrest the waterworks engineers", "Somebody must pay.",
                 {"morale": -3, "outbreak": {"disease": "cholera", "city": "Aldmark"}})],
         chain_only=True),
    card("summer_fires", "Fires in the Stonereach", "CRISIS",
         "A dry summer has set the Stonereach forests alight. The fires are racing toward the villages and the "
         "Stanhope rail tunnel.",
         [choice("army", "Send the army to fight the fires", "Soldiers with shovels, not rifles.",
                 {"army_morale": -2, "morale": 2}),
          choice("burn", "Let it burn out", "The mountains will recover.", {"morale": -2, "stockpiles": {"grain": -2000}})],
         conditions={"seasons": ["Summer"]}),
    card("war_orphans", "The War Orphans", "CRISIS",
         "The charities can no longer cope with the children of the dead: orphans are sleeping under the railway "
         "arches of every city.",
         [choice("orphanages", "State orphanages", "Every child fed and schooled.", {"treasury": -25000, "morale": 2}),
          choice("adoption", "A national adoption drive", "Every family can take one more.", {"morale": 1}),
          choice("nothing", "The state cannot afford it", "The war comes first.", {"morale": -3})],
         conditions={"min_turn": 12}),
]


# ================================================================== WRITER PACKS

WRITER_CARD_KEYS = {"id", "title", "category", "weight", "description", "text", "condition", "conditions",
                    "is_chain_only", "chain_only", "choices", "repeatable"}
WRITER_CHOICE_KEYS = {"id", "label", "hint", "effects", "follow_up", "follow_ups"}

WARNINGS: list[str] = []


class WriterPackError(ValueError):
    pass


def _lookup(value: str, table: dict[str, str], what: str, where: str) -> str:
    """Resolve an id or a display name (case-insensitive) against {id: name}."""
    key = value.strip().lower()
    for id_, name in table.items():
        if key in (id_.lower(), name.lower(), id_.replace("_", " ").lower()):
            return id_
    raise WriterPackError(f"{where}: unknown {what} {value!r} (known: {', '.join(sorted(table.values()))})")


def _named_city(texts: list[str], cities: list[str]) -> str | None:
    """The first Kestrian city named in the first text that names one."""
    for text in texts:
        found = [(m.start(), c) for c in cities for m in [re.search(rf"\b{re.escape(c)}\b", text)] if m]
        if found:
            return min(found)[1]
    return None


def translate_card(raw: dict, ctx: dict, parent_text: str = "") -> dict:
    where = f"writer card [{raw.get('id')}]"
    unknown = set(raw) - WRITER_CARD_KEYS
    if unknown:
        raise WriterPackError(f"{where}: unknown card keys {sorted(unknown)}")
    text = raw.get("text") or raw.get("description") or ""
    out = {"id": raw["id"], "title": raw["title"],
           "category": raw.get("category") or ctx["parent_category"].get(raw["id"], "CLASSIFIED"),
           "weight": raw.get("weight", 5), "text": text, "choices": []}
    conditions = dict(raw.get("conditions") or raw.get("condition") or {})
    if "tax_policy_condition" in conditions:
        policies = conditions.pop("tax_policy_condition")
        policies = [policies] if isinstance(policies, str) else policies
        conditions["tax_policies"] = [_lookup(v, ctx["tax"], "tax policy", where) for v in policies]
    if conditions:
        out["conditions"] = conditions
    if raw.get("chain_only", raw.get("is_chain_only")):
        out["chain_only"] = True
    if raw.get("repeatable"):
        out["repeatable"] = True
    for index, ch in enumerate(raw["choices"]):
        cwhere = f"{where} choice {index + 1}"
        unknown = set(ch) - WRITER_CHOICE_KEYS
        if unknown:
            raise WriterPackError(f"{cwhere}: unknown choice keys {sorted(unknown)}")
        effects: dict = {}
        for key, value in (ch.get("effects") or {}).items():
            if key == "civil_morale":
                effects["morale"] = effects.get("morale", 0) + value
            elif key == "change_tax_policy":
                effects["tax_policy"] = _lookup(value, ctx["tax"], "tax policy", cwhere)
            elif key == "grant_technology":
                effects["tech"] = _lookup(value, ctx["techs"], "technology", cwhere)
            elif key == "start_outbreak":
                spec = value if isinstance(value, dict) else {"disease": value}
                city = spec.get("city") or _named_city([text, parent_text], ctx["cities"])
                effects["outbreak"] = {"disease": _lookup(spec["disease"], ctx["diseases"], "disease", cwhere)}
                if city:
                    effects["outbreak"]["city"] = city
                else:
                    WARNINGS.append(f"{cwhere}: no city named — the outbreak starts wherever the disease would")
            elif key == "trigger_disaster":
                spec = value if isinstance(value, dict) else {"kind": value}
                region = spec.get("region")
                if not region:
                    city = _named_city([text, parent_text], ctx["cities"])
                    region = ctx["city_region"][city] if city else None
                effects["disaster"] = {"kind": _lookup(spec["kind"], ctx["disasters"], "disaster", cwhere)}
                if region:
                    effects["disaster"]["region"] = _lookup(region, ctx["regions"], "region", cwhere)
                else:
                    WARNINGS.append(f"{cwhere}: no place named — the disaster strikes wherever the terrain suits it")
            else:
                effects[key] = value  # engine keys pass through (validated with the deck)
        follow = ch.get("follow_ups") or ch.get("follow_up") or []
        follow = [follow] if isinstance(follow, dict) else follow
        follow_ups = [{"card": f["event_id"], "delay_weeks": f.get("delay_weeks", 1)} if "event_id" in f else f
                      for f in follow]
        entry = {"id": ch.get("id") or f"option_{index + 1}", "label": ch["label"], "hint": ch.get("hint", ""),
                 "effects": effects}
        if follow_ups:
            entry["follow_ups"] = follow_ups
        out["choices"].append(entry)
    return out


def load_writer_packs() -> list[dict]:
    """Every tools/writer_packs/*.json, translated into the engine schema."""
    from src.engine.data_loader import new_game

    state = new_game(seed=0)
    world = state.world_map
    ctx = {
        "tax": {p["id"]: p["name"] for p in state.config["economy"]["tax_policies"]},
        "techs": {t["id"]: t["name"] for t in state.catalog["tech_tree"]["techs"]},
        "diseases": {k: v["name"] for k, v in state.catalog["crises"]["diseases"].items()},
        "disasters": {k: v["name"] for k, v in state.catalog["crises"]["disasters"].items()},
        "regions": {r.id: r.name for r in world.regions.values()},
        "cities": sorted(state.cities, key=len, reverse=True),
        "city_region": {f.name: world.region_at(f.x, f.y).id for f in world.features
                        if f.name in state.cities and world.region_at(f.x, f.y)},
    }
    raws = []
    for path in sorted(PACKS.glob("*.json")):
        raws += json.loads(path.read_text(encoding="utf-8"))
    parents, parent_of = {}, {}
    for raw in raws:  # chain cards inherit the category (and place names) of the card that leads to them
        for ch in raw["choices"]:
            follow = ch.get("follow_ups") or ch.get("follow_up") or []
            for f in [follow] if isinstance(follow, dict) else follow:
                target = f.get("event_id") or f.get("card")
                parents.setdefault(target, raw.get("description") or raw.get("text") or "")
                parent_of.setdefault(target, raw["id"])
    by_id = {raw["id"]: raw for raw in raws}
    categories = {}
    for card_id in parent_of:  # walk up the chain to the first card with a category
        seen, cursor = set(), card_id
        while cursor in parent_of and cursor not in seen and not by_id.get(cursor, {}).get("category"):
            seen.add(cursor)
            cursor = parent_of[cursor]
        categories[card_id] = by_id.get(cursor, {}).get("category", "CLASSIFIED")
    ctx["parent_category"] = categories
    return [translate_card(raw, ctx, parents.get(raw["id"], "")) for raw in raws]


def main() -> None:
    sys.path.insert(0, str(ROOT))
    data = json.loads(DECK.read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in data["cards"]}
    for c in data["cards"]:  # normalise the older categories
        c["category"] = CATEGORY_MAP.get(c.get("category", ""), c.get("category", "DOMESTIC"))
    for c in CARDS:
        by_id[c["id"]] = c
    writer = load_writer_packs()
    for c in writer:  # the Head Writer's cards win over placeholders
        by_id[c["id"]] = c
    for retired in RETIRED:
        by_id.pop(retired, None)
    data["cards"] = list(by_id.values())
    comment = [line for line in data["_comment"] if "EVENT CHAINS" not in line]
    comment.append("EVENT CHAINS: a choice may carry follow_ups, same schema as email follow-ups with `card` for "
                   "`email`: [{card, delay_weeks}] or [{delay_weeks, chance, outcomes: [{card, weight}]}]. Cards marked "
                   "chain_only are only ever scheduled. Generated/extended by tools/generate_massive_deck.py.")
    data["_comment"] = comment

    from src.engine.data_loader import load_json, validate_deck  # validate before writing

    resources = {r["id"] for r in load_json("resources.json")["resources"]}
    equipment = {e["id"] for e in load_json("equipment.json")["equipment"]}
    validate_deck(data, resources, equipment)
    chains = sum(1 for c in data["cards"] for ch in c["choices"] for _ in ch.get("follow_ups", []))
    tmp = DECK.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(DECK)
    categories: dict[str, int] = {}
    for c in data["cards"]:
        categories[c["category"]] = categories.get(c["category"], 0) + 1
    print(f"writer packs: {len(writer)} cards ({', '.join(c['id'] for c in writer)})")
    for warning in WARNINGS:
        print(f"  note: {warning}")
    print(f"{len(data['cards'])} cards ({sum(1 for c in data['cards'] if c.get('chain_only'))} chain-only), "
          f"{chains} event-chain links. By category: {categories}")


if __name__ == "__main__":
    main()

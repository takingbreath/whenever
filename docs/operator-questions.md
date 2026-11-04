# What a restaurant operator asks of the cameras

The people who run a restaurant, or watch its cameras, ask the same kinds of questions every day. This page lists them by place, and says which Whenever block answers each one. Every answer here works for other places too: a shop, a clinic, a parking lot or an office.

**Legend.** *Free* runs on the server's CPU with the local detector, and costs no model call. *Paid* asks Spark, usually only when a free count or a timer says that one place may need attention. *Not built* has a reason.

## The dining room

| Question | How Whenever answers | Cost |
|---|---|---|
| How many guests are in now, at each table, in each zone? | `ctx.places("table")`, `ctx.detect("person", places=...)`, `ctx.steady` | Free |
| Which tables are free right now? | The same count, shown live with `ctx.show` | Free |
| How long do guests stay? What is the turnover of each table today? | `ctx.visit` for each table, recorded when a visit ends | Free |
| A table has waited N minutes and nothing is served | `ctx.since` for each seated table, then one close-up `ctx.check(place=table)` | Paid, once per wait |
| Guests call for staff (a raised hand) | `ctx.detect(pose=True)`, `hand_up` on the same id for a few looks, then a close-up check | Free, then paid |
| A table stays dirty after guests leave | When `ctx.visit` ends, a close-up check: "This table is clean and empty" | Paid, once per visit |
| A large group sits down | A steady count of 3 or more at one table | Free, one check |
| A guest left a bag, phone or laptop behind | `handbag`, `backpack`, `laptop` or `cell phone` at a place with no person | Free, then paid |
| Too many people for the room (fire safety) | A count of the whole view | Free |
| Somebody falls or lies on the floor | `pose=True` and `lying`, then a close-up check and an alert at once | Free, then paid |

## The entrance and the queue

| Question | How Whenever answers | Cost |
|---|---|---|
| How many people came in and went out, by hour? | `ctx.crossings(Line(...))` across the door | Free |
| How big are the groups that come in? | Crossings less than a few seconds apart | Free |
| Is a line forming at the door or the counter? How long is it? | A zone from `beside(counter, "below")`, a steady count | Free |
| How long do people wait before they are seated or served? | `.stayed` of each person in the zone, or the queue length over the people served | Free |
| Do people leave the line without being served? | A person who leaves the zone away from the counter | Free, rough |
| Is the host stand staffed? | `ctx.visit` of the host stand, with gaps recorded | Free |

## Outside

| Question | How Whenever answers | Cost |
|---|---|---|
| How many people pass, and how many of them come in? | One line on the pavement, one at the door | Free |
| Which parking spaces are taken? Is a car blocking the entrance? | `car` at `ctx.places("parking space")`, or at a zone | Free |
| How long do delivery riders wait for their orders? | `motorcycle` and `person` at the pickup zone, with `ctx.visit` | Free |
| Was the shutter opened on time? Are the lights off at closing? | A schedule and one `ctx.check` | Paid, once a day |
| Is someone loitering after closing time? | A count in a schedule window, and `.stayed` | Free, then paid |
| Is the bin overflowing, or the front dirty? | A sensor with a change wake | Paid, only on change |

## The kitchen and hygiene

| Question | How Whenever answers | Cost |
|---|---|---|
| Do cooks wear caps, gloves and masks? | A close-up `ctx.check` of each cook, a few times an hour | Paid |
| A spill, smoke, food left out, a fridge door left open | A sensor with a change wake | Paid, only on change |
| How many cooks are at the stations at the peak? | A count in the kitchen camera | Free |
| Rats or cockroaches at night | A sensor with Spark at night. The detector knows cats, dogs and birds, not rats | Paid |

## Staff, cash and security

| Question | How Whenever answers | Cost |
|---|---|---|
| How many staff are on the floor against how many guests? | A count of each, with a close-up check of a few people for uniforms | Mostly free |
| A waiter uses a phone while guests wait | A close-up check when a wait is long | Paid, rare |
| The cash drawer is open with nobody at the counter | A count of zero at the counter, then one check of the drawer | Free, then paid |
| The back door is open, or someone enters after hours | A change wake on the door, or a count in a schedule window | Paid, only on change |
| A fight | A sensor with Spark, woken by a change | Paid |

## Reports

At closing time a rule reads `ctx.history`, does the arithmetic in Python, draws a chart and sends it: guests per hour, the peak, the average stay, the turnover of each table, the longest waits, footfall and how many passers-by came in, and when the host stand had nobody. `Ask` answers one-off questions about the last hours, such as "when did the supplier come?".

## Telling who someone is

Some operators want to know when a person they banned comes back, or when a regular guest arrives. Whenever does not do this now, and its rules tell Wonder never to try.

- **The Meta models.** SAM finds faces and outlines them. It does not say whose face it is. We did not test whether Spark would name a person, and its makers' policies limit that kind of use.
- **Open-source tools.** Face matching exists with free licences. OpenCV's YuNet finds faces (MIT licence), and OpenCV's SFace turns a face into numbers to compare (its folder carries an Apache-2.0 licence). OpenCV does not say what SFace was trained on. InsightFace's code is MIT, but its trained models are for non-commercial research only, so they do not fit.
- **The law.** In India, the Digital Personal Data Protection Act 2023 treats a face as personal data. Commentators say that a business needs clear consent for face matching, and that consent for security cameras does not cover it. Other places are stricter: Illinois needs written consent, and Portland bans face recognition in shops and restaurants. A lawyer must check each country before anyone uses it.
- **Accuracy.** Ceiling cameras see faces small, from above, and at night in infrared. A false match makes staff confront the wrong guest, which hurts a real person and the business.

If we build it later, it must be off by default, use only photos that the owner adds of people the owner banned, show a sign at the door, ask a person to confirm every match before anything happens, and delete what it does not need.

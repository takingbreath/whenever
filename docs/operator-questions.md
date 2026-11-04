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

## Knowing people again

An operator can tag any person from a picture, with a name or a label, such as "Ravi", "Regular 3", "Waiter Asha" or "Banned 1". Whenever then knows them again, and rules can follow them by name.

| Question | How Whenever answers | Cost |
|---|---|---|
| A regular guest, a supplier or a person who is banned comes in | `ctx.who` on the people at the door, then an alert with the name and picture | Free |
| When did this person last come? How often this month? | `ctx.people()` and each sighting that `ctx.who` keeps | Free |
| Leave staff out of guest counts, and measure their time on the floor | Tag each waiter once, then `ctx.who` on the people at each place | Free |
| A guest raises a hand: alert the waiter, then the manager if nobody comes | `pose=True` and `hand_up`, one close-up check, `ctx.notify(to="Waiter")`, then `to="Manager"` | Free, then paid once |

**How it knows a person.** By face across days and weeks, and by look (clothes and build) within a day.

- **Faces.** YuNet (OpenCV Zoo, MIT) finds the face. ArcFace R50 (InsightFace) compares it. We tagged 3 people with 3 pictures each, from one day, in the webcam sessions of 30 October to 2 November. On other days, it named them right 97.9% of the time, never gave a wrong name, and gave no name to 97 faces of people who were not tagged. Shrunk to 24 pixels across, faces still matched 96% of the time at 1 false match in 1,000.
- **ArcFace's weights are for non-commercial use.** `WHENEVER_FACE_MODEL=sface` uses OpenCV's SFace (Apache-2.0) instead. On the same test it named people right 90.3% of the time, with no wrong names.
- **Looks.** Meta's PE-Core compares the person's whole box. On our gate camera it found the same person as the nearest picture in 38 of 39 pictures, between night and day. But other people in similar clothes came close, so a look counts only within 12 hours of a sighting, and only as a hint.
- **A person who turns away keeps the name** while the tracker follows them, after one face match. The name also goes back to the minutes before that clear look.
- **How sure, as a percentage.** Each name shows how sure Whenever is, from the face score and the face size. On our test, an ArcFace score of 0.36 or more named the right person every time, and a score below 0.25 was always a stranger. Between them, Whenever shows a lower percentage, such as 72%. A name carried by clothes is less sure again: 0.9 times on the same camera, 0.8 times on another camera.
- **Choose by purpose.** Greeting a regular or leaving staff out of counts can act from about 70%. A banned person gets an alert that says "Maybe" with both faces side by side, and a person decides. Every "Yes, same person" or "Not this person" teaches the percentages at that site.
- **Regulars with no name.** A clear face that is no tagged person becomes "Visitor 37", with its visits. Name it later, and its past visits stay. Visitors that nobody names are forgotten after 30 days.

**Where it does not work.** It needs a face at least about 20 pixels across. Our gate camera hangs high and sees people from above or from behind: 12 to 27 pixels, and mostly no face at all. A camera at head height near a door sees most faces.

**The law.** In India, the Digital Personal Data Protection Act 2023 treats a face as personal data, and commentators say that face matching needs clear consent. Check the law of each country, put a sign at the door, and forget a person when they ask.

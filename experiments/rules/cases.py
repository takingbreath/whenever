"""Eight sentences, with simulated sensor events and known correct answers."""

# role: words that identify the sensor the model chose.
# events: (seconds since midnight, role, "on" or "off").
# want: the times an action must happen. slack_s allows for a once-a-second clock.
CASES = [
    {
        "name": "count_every_5th",
        "sentence": "Count how many times I drink water and tell me on every 5th time.",
        "roles": {"drink": "drink|sip"},
        "events": [(t, r, s) for i in range(1, 13) for t, r, s in
                   ((i * 100, "drink", "on"), (i * 100 + 3, "drink", "off"))],
        "want": [500, 1000],
    },
    {
        "name": "count_to_three",
        "sentence": "When the pressure cooker whistles 3 times, notify me.",
        "roles": {"whistle": "whistle|steam"},
        "events": [(60, "whistle", "on"), (63, "whistle", "off"), (200, "whistle", "on"), (203, "whistle", "off"),
                   (400, "whistle", "on"), (403, "whistle", "off")],
        "want": [400],
    },
    {
        "name": "long_sit",
        "sentence": "If I stay seated at my desk for more than 50 minutes, tell me to take a break.",
        "roles": {"sit": "sit|seat|desk|chair"},
        "events": [(0, "sit", "on"), (1200, "sit", "off"), (1300, "sit", "on"), (5000, "sit", "off")],
        "want": [4300],
    },
    {
        "name": "sight_then_sound",
        "sentence": "When the red cup is lifted and a bell rings within ten seconds, say hello.",
        "roles": {"cup": "cup", "bell": "bell"},
        "events": [(100, "cup", "on"), (103, "cup", "off"), (120, "bell", "on"), (121, "bell", "off"),
                   (200, "cup", "on"), (203, "cup", "off"), (205, "bell", "on"), (206, "bell", "off"),
                   (300, "bell", "on"), (301, "bell", "off")],
        "want": [205],
    },
    {
        "name": "customer_wait",
        "sentence": "If a customer waits at the counter for more than 3 minutes, notify the manager.",
        "roles": {"customer": "customer|wait|person|counter"},
        "events": [(100, "customer", "on"), (200, "customer", "off"), (500, "customer", "on"), (800, "customer", "off")],
        "want": [680],
    },
    {
        "name": "missing_step",
        "sentence": "If someone seals a package without putting the manual inside, warn me.",
        "roles": {"manual": "manual", "seal": "seal|tape|clos"},
        "events": [(100, "manual", "on"), (102, "manual", "off"), (110, "seal", "on"), (112, "seal", "off"),
                   (200, "seal", "on"), (202, "seal", "off"),
                   (300, "manual", "on"), (302, "manual", "off"), (310, "seal", "on"), (312, "seal", "off")],
        "want": [200],
    },
    {
        "name": "rate_window",
        "sentence": "Tell me if I pick up my phone more than 5 times in 10 minutes.",
        "roles": {"phone": "phone"},
        "events": [(t, r, s) for b in (0, 100, 200, 300, 400, 500, 2000) for t, r, s in
                   ((b + 1, "phone", "on"), (b + 3, "phone", "off"))],
        "want": [501],
    },
    {
        "name": "time_of_day",
        "sentence": "Alert me if the door stays open for more than 5 minutes after 10 pm.",
        "roles": {"door": "door"},
        "events": [(72000, "door", "on"), (72900, "door", "off"), (81000, "door", "on"), (81600, "door", "off")],
        "want": [81300],
    },
]

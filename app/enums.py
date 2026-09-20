"""Value sets behind the text + CHECK columns, plus the groups the rules reason about."""

USER_ROLES = ("learner", "mentor", "support", "staff")
STAFF_ROLES = frozenset({"support", "staff"})

CHALLENGE_DIFFICULTIES = ("Beginner", "Intermediate", "Advanced", "Expert")
CHALLENGE_STATUSES = ("published", "draft", "archived")

CONVERSATION_STATUSES = ("open", "answered", "resolved", "closed")
TERMINAL_STATUSES = frozenset({"resolved", "closed"})
CONVERSATION_PRIORITIES = ("low", "medium", "high", "urgent")

REACTION_TYPES = ("upvote", "helpful")

# Categories are not CHECK constrained: they are the forum's own taxonomy, and a new one
# appearing in a data refresh must not fail the seed. POST /conversations validates against
# CONVERSATION_CATEGORIES instead.
CHALLENGE_CATEGORIES = (
    "Algorithms",
    "Compilation",
    "Error Correction",
    "Getting Started",
    "Hamiltonians",
    "Hardware",
    "Optimization",
    "Photonics",
    "Quantum Chemistry",
    "Quantum Circuits",
    "Quantum Information",
    "Quantum Machine Learning",
)

CONVERSATION_CATEGORIES = (
    "Announcements",
    "Borealis",
    "Catalyst",
    "Codebook",
    "Demos",
    "FlamingPy",
    "PennyLane Challenges",
    "PennyLane Development",
    "PennyLane Feedback",
    "PennyLane Help",
    "PennyLane Plugins",
    "Photonic Software",
    "Quantum Compilation",
)

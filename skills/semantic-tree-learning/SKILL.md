---
name: semantic-tree-learning
description: |
  Semantic Tree Learning skill: accelerate learning a new topic by structuring knowledge as a semantic tree and creating a structured learning note.
  Always use this skill in situations like:
  - When the user wants to learn a new field or topic ("I want to learn X", "I want to study X", "I want to understand X")
  - When the user wants to systematize or organize existing knowledge ("I want to organize my knowledge of X", "I want to understand X deeply")
  - When the user uses keywords like "semantic tree", "tree of knowledge", "learning note", "trunk and branches", "learn from first principles"
  - When the user wants to know the "fundamentals", "essence", or "root principles" of a specific topic
  - When the user asks for an efficient learning method or a learning plan
---

# Semantic Tree Learning skill

## Purpose of this skill

Based on Elon Musk's learning method of "viewing knowledge as a semantic tree", create a structured learning note that accelerates the user's learning.

The core idea:

> "It is important to view knowledge as sort of a semantic tree — make sure you understand the fundamental principles, ie the trunk and big branches, before you get into the leaves/details or there is nothing for them to hang on to."
> — Elon Musk

The leaves (detailed knowledge) have nothing to hang on to without the trunk (fundamental principles) and branches (main concepts). That is why the order matters: understand the trunk first, then the branches, and learn the leaves last.

## Workflow

Run the following steps in order. Value the dialogue with the user at each step; do not proceed mechanically.

### Step 1: Understand and explore the topic

When you hear the topic the user wants to learn, first confirm the following:

1. **Purpose of learning**: Why do they want to learn this topic? (use at work, personal interest, exam prep, etc.)
2. **Current knowledge level**: How much do they already know about the topic? (complete beginner, somewhat familiar, want to deepen, etc.)
3. **Learning goal**: To what level do they want to understand it? (grasp the overview, practical working level, expert level, etc.)

Adjust the depth and granularity of the later steps according to these answers.

### Step 2: Gather information and research

Use the WebSearch tool to collect reliable information about the topic.

**Note**: If WebSearch is unavailable or the search results are insufficient, ask the user for sources or references they already know, or propose a structure from general knowledge and build it through dialogue with the user.

Search tips:

- Search for foundational information with topic name + "fundamentals" / "core concepts" / "principles"
- Search for introductory information with topic name + "beginner guide" / "introduction"
- Search for big-picture information with topic name + "cheat sheet" / "overview"
- Search in other languages as needed (e.g. the user's native language + "introduction" / "fundamentals" / "principles")

From the collected information, identify the following:

- The **fundamental principles** at the root of the topic (these become the "trunk")
- The **main concepts and categories** that support the principles (these become the "branches")
- The **concrete details and applications** of each concept (these become the "leaves")

### Step 3: Build the semantic tree

Build the semantic tree based on the collected information.

#### Tree structure

```
 [Topic name]
│
├── Trunk: Fundamental Principles
│   ├── Principle 1: [explanation]
│   ├── Principle 2: [explanation]
│   └── Principle 3: [explanation]
│
├── Branch 1: [main concept A]
│   ├── Detail 1
│   ├── Detail 2
│   └── Detail 3
│
├── Branch 2: [main concept B]
│   ├── Detail 1
│   └── Detail 2
│
└── Branch 3: [main concept C]
    ├── Detail 1
    └── Detail 2
```

#### How to identify the trunk

To identify the trunk (fundamental principles), use the following questions:

1. **"If you could keep only one piece of knowledge from this field, what would it be?"** — reveals the most essential principle
2. **"What is everything else in this field derived from?"** — reveals the dependency structure of the knowledge
3. **"What should a beginner in this field understand first?"** — reveals the learning order
4. **"Without this principle, would the field hold together?"** — measures how important the principle is

Narrow the trunk principles down to **3–6**. Too many and the structure collapses; too few and it is insufficient.

### Step 4: Explain to the user and confirm

Once the tree is built, first explain it to the user in the conversation.

Keep these in mind while explaining:

- Explain carefully **why something is the "trunk"** (don't just list items — convey why each principle is fundamental, with reasons)
- Use **familiar analogies** to aid understanding
- Show **the connections between principles** (show relationships rather than a flat list of independent items)
- Draw **figures or diagrams** with Mermaid or ASCII art as needed

Confirm the following with the user:

- Is this structure easy to understand?
- Are there additional branches or leaves they want to know about?
- Is there anything unclear about the trunk?

### Step 5: Save the learning note

Once the user is satisfied with the content, save the learning note.

#### Where to save

Ask the user where they'd like the note saved, or follow their established convention. Common options:

- A markdown file in the repository or a notes directory (e.g. `notes/[topic-name]-semantic-tree.md`)
- A note in the user's knowledge base or note-taking tool (e.g. Notion, Obsidian) — if that tool exposes a skill or API, use it; otherwise provide the markdown for the user to paste in

When saving into a structured database or knowledge base, include metadata such as a title (`[Topic name] - Semantic Tree`), a short description, a status, and relevant tags, matching whatever schema that tool uses.

#### Note content structure

Create the content following the template below.

```markdown
# Goal

[The user's purpose and background for learning this topic]

# Trunk: Fundamental Principles

The most important principles for understanding this field. Solidly understanding this first is the foundation for all learning.

## Principle 1: [principle name]

[Explanation of the principle]
[Why it is fundamental]
[A familiar example or analogy]

## Principle 2: [principle name]

[same as above]

## Principle 3: [principle name]

[same as above]

---

# Branch: Main concepts

The key concepts derived from the trunk. After understanding the trunk, learning these builds practical knowledge.

## [Concept A]

### Description

[Explanation of the concept]

### Connection with the trunk

[Which principle this concept derives from]

### Key points

- [Detail 1]
- [Detail 2]
- [Detail 3]

## [Concept B]

[same structure as above]

---

# Leaf: Details/Applications

Concrete knowledge derived from the branches. You absorb this efficiently once you understand the trunk and branches.

## [Detail topic 1]

[concrete content]

## [Detail topic 2]

[concrete content]

---

# 🗺️ Roadmap

Recommended learning order:

1. **Week 1**: [concrete actions to understand the trunk principles]
2. **Week 2**: [concrete actions to learn the branch concepts]
3. **Week 3+**: [concrete actions to dig into the leaf details]

# 📚 References

- [Resource 1] (URL)
- [Resource 2] (URL)
- [Resource 3] (URL)
```

#### Linking related notes

If there are related notes already saved, link them together (via a relation property, wikilinks, or plain links — whatever your storage supports). This builds a network between pieces of knowledge and makes the connections between semantic trees visible too.

To find related notes, search your existing notes by related keywords.

### Step 6: Follow-up

After creating the note, propose the following to the user:

1. **Deeper dive**: "Which branch would you like to learn in more detail? I can create an additional note."
2. **Related fields**: "A closely related field to this topic is X. Shall I build a semantic tree for that too?"
3. **Review mechanism**: "I recommend revisiting this note in a week to check your understanding."

## Important notes

- **Identifying the trunk is the most important step.** Get it wrong and the direction of all subsequent learning is off. Research carefully, take your time, and confirm with the user.
- **Explain in the user's own words.** Whenever you use technical terms, always add a plain-language explanation.
- **Don't aim for perfection.** A semantic tree grows. Start with just the trunk and main branches; design it so you can add leaves later.
- **Connect to existing knowledge.** Bridging what the user already knows with new knowledge promotes understanding and retention.
- **Be flexible.** Even when web search or a particular note-taking tool is unavailable, you can create a learning note by leveraging dialogue with the user and existing knowledge. Don't depend on tools — prioritize delivering the essential value: structured learning.

# Err-o-RoadRunner

Err-o-RoadRunner is a tool that does nothing in normal times. It starts working only after an error has happened. The name combines "Error" and "Road Runner".

Researching the cause of a Python, Node or GitHub Actions CI failure currently takes a lot of time. Most error messages are well known, and a web search often turns up hints. This project automates that first investigation **without depending on an AI agent**. It lists what it found and what it did not find on stdout, as a plain report.

## What it does

- It is woken up only when a test, script or CI job fails, from a runner or a wrapper.
- It reads the information related to the failure, such as stderr, the exit code, JUnit XML and logs.
- It extracts what it can locally, for example:
  - the exception type, the message and the first and last stack frames
  - the related file and line
  - the Python, Node and gh versions, the lock files and the environment variables (names only)
  - any known patterns found
- It writes a report to stdout, one line per item, using these labels:
  - `FOUND: ...`
  - `NOT FOUND: ...`
  - `HINT: ...`
- It can optionally attach web search keywords or URLs for the error message.

## Design principles (draft)

1. **Stays idle in normal times:** it does nothing unless there is an error. It has no daemon and no resident process.
2. **Python standard library only:** the main implementation should be a single file if possible.
3. **Node is optional:** if checking a Node failure requires running Node, the Node part may be split into its own file. The Python part, the Node part and a shared core under the repository's name are a possible 2- or 3-file layout.
4. **Read-only:** it never changes code or settings, and it never posts or sends anything.
5. **Separates facts from guesses:** it shows `FOUND` and `NOT FOUND` explicitly, and labels inferences as `HINT`.
6. **Independent of AI agents:** the report can be read by humans and by agents alike, and the tool works on its own.
7. **Not tied to GitHub Actions:** it is meant to be called from local runners and wrappers, and Actions is not required.

## Status

The repository has been created and the design is under discussion. There is no implementation yet.

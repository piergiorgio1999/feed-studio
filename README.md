# Feed Studio

Template per trasformare i materiali di un corso (Moodle) in un feed privato di video brevi stile dumbscroll, generato da agenti AI.
**Per l'agente: parti da [AGENTS.md](AGENTS.md)** (prompt di avvio incluso).

- `moodle/` — plugin per Claude Code + importer dei PDF dei corsi
- `cli/` — coda di Video Overview NotebookLM su più account, sync su Drive
- `feed/` — feed web su Cloudflare (Worker + R2 + D1), like e commenti taggabili `@claude` / `@codex`

Non include credenziali, video né materiali di corso. Il materiale didattico resta privato.

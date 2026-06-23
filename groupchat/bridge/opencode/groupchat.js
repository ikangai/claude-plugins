/**
 * groupchat — experimental opencode bridge for the shared chat bus.
 *
 * The bus is `.groupchat/chat.py` (a dependency-free Python CLI). opencode has no
 * documented pre-message hook yet, so this plugin cannot auto-inject teammate
 * messages straight into the model the way the Claude Code / Codex hooks do
 * (tracked upstream: opencode #5409). Until then it does the parts that ARE
 * reliable with the documented plugin API:
 *
 *   - `shell.env`  → export GROUPCHAT_SESSION so every shell the agent runs shares
 *                    ONE identity with the AGENTS.md instructions (no double agent).
 *   - `event`      → on `session.idle` (a turn just ended) auto-register and PEEK
 *                    the inbox (never advancing the single read cursor), nudging
 *                    when a teammate @mentions this agent.
 *
 * The authoritative, cursor-advancing fetch is the agent itself running
 * `chat.py read` per the AGENTS.md block that `install.py opencode` writes
 * alongside this file. Two surfaces (this nudge + the agent's read), one cursor.
 *
 * Fail-open by construction: every path is wrapped so a missing bus or a CLI error
 * can never break an opencode session.
 */
export const GroupChat = async ({ $, directory, worktree }) => {
  const root = String(worktree || directory || ".");
  const chat = `${root}/.groupchat/chat.py`;
  // opencode gives no session id at init, so derive a stable one from the project
  // path: one opencode session per project dir keeps a stable handle across resumes.
  const sid = "opencode-" + root.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 80);
  let registered = false;

  async function register() {
    if (registered) return;
    try {
      // --no-barrier: opencode has no Stop hook, so this agent never marks done; the
      // flag keeps it from holding a hook (Claude/Codex) team at the team barrier.
      await $`python3 ${chat} register --session ${sid} --cwd ${root} --no-barrier`.quiet();
      registered = true;
    } catch {
      /* fail open — never break an opencode session */
    }
  }
  await register();

  return {
    // Share ONE identity with the AGENTS.md floor: the agent's own `chat.py` calls
    // pick up this session id from the environment instead of inventing a second one.
    "shell.env": async (_input, output) => {
      try {
        output.env.GROUPCHAT_SESSION = sid;
      } catch {
        /* fail open */
      }
    },

    // On idle, surface @mentions without consuming them (peek = don't advance the
    // cursor). The agent's `chat.py read` remains the single cursor's owner.
    event: async ({ event }) => {
      try {
        if (!event || event.type !== "session.idle") return;
        await register();
        const out = (
          await $`python3 ${chat} inbox --session ${sid} --peek`.text()
        ).trim();
        if (out && !out.startsWith("(no unread")) {
          console.log(
            "📨 group chat — you're mentioned; run `chat.py read` to reply:\n" + out,
          );
        }
      } catch {
        /* fail open */
      }
    },
  };
};

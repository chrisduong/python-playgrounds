# terraform-ls-debuggers

A minimal LSP client for probing `terraform-ls` directly over stdio, bypassing
the editor entirely. Useful when hover, completion, or other language-server
behavior looks wrong and you want to know whether the bug is in `terraform-ls`
itself or in the editor extension talking to it.

`lsp_hover_probe.py` currently probes `textDocument/hover`: it launches
`terraform-ls serve` as a subprocess, drives the LSP handshake
(`initialize` → `initialized` → `didOpen` → `hover` → `shutdown` → `exit`),
and prints the raw hover response for each position you ask about.

## Requirements

- Python 3 (standard library only — no `pip install` needed).
- A `terraform-ls` binary on disk. You don't need to run or install it
  separately: the script starts it itself. Common places to find one:
  - Bundled with the VS Code Terraform extension:
    `~/.vscode/extensions/hashicorp.terraform-<version>-<platform>/bin/terraform-ls`
  - A standalone install (e.g. via `brew install hashicorp/tap/terraform-ls`),
    findable with `which terraform-ls`.

You do **not** need to "start the language server" as a separate step —
`terraform-ls serve` just means "run in language-server mode, listening on
stdin/stdout," which is exactly what any editor does when it launches it.
The script does that launch for you.

## Usage

```
python3 lsp_hover_probe.py \
  --tfls /path/to/terraform-ls \
  --workdir /path/to/module/dir \
  --file locals.tf \
  --at 46:concat --at 29:merge
```

- `--tfls` — path to the `terraform-ls` binary.
- `--workdir` — the module/workspace directory to open (used as the LSP
  `rootUri`).
- `--file` — the file to open, relative to `--workdir`.
- `--at LINE:WORD` — hover at the first occurrence of `WORD` on that
  1-indexed line. Repeatable.
- `--at LINE:COL` — hover at an exact 1-indexed line / 0-indexed character,
  if you'd rather not rely on word search.
- `--settle` (default `6.0`) — seconds to wait after opening the file before
  sending hover requests. `terraform-ls` indexes the module asynchronously;
  hovering too early races that and produces a spurious
  `-32800 file not found` error instead of a real result.
- `--timeout` (default `10.0`) — seconds to wait for each hover response.
- `--log-file` (default `/tmp/terraform-ls-probe.log`) — where to write
  `terraform-ls`'s own debug log, useful for correlating a `None` result with
  what the server actually did internally.

Each line of output looks like:

```
line   46 col  18 concat          -> {'contents': {...}, 'range': {...}}
```

or `None` if the server has no hover data for that position, or
`TIMEOUT (no response)` if it never replied.

## Example: reproducing a real hover bug

`example/main.tf` is a minimal repro of a `terraform-ls` bug found with this
tool: hover works for a function call when it's the direct value of a
top-level `local`, but returns `null` when the exact same call is nested
inside an object-constructor attribute value (e.g. `{ Statement = fn(...) }`)
— regardless of which function it is.

```hcl
locals {
  # Top-level calls: hover works for these.
  a = concat([1, 2], [3, 4])
  b = merge({ x = 1 }, { y = 2 })

  # Same functions, but as the value of an attribute inside an
  # object-constructor expression: hover returns null for these.
  s = {
    Statement = concat(
      [1, 2],
      [3, 4],
    )
  }
  t = {
    Statement = merge(
      { x = 1 },
      { y = 2 },
    )
  }
}
```

Run the probe against it:

```
python3 lsp_hover_probe.py \
  --tfls /path/to/terraform-ls \
  --workdir "$(pwd)/example" \
  --file main.tf \
  --at 3:concat --at 4:merge --at 9:concat --at 15:merge
```

Expected output — the top-level calls (lines 3, 4) return full markdown docs,
the nested ones (lines 9, 15) return `None`:

```
line    3 col   9 concat          -> {'contents': {'kind': 'markdown', 'value': '```terraform\nconcat(…seqs dynamic) dynamic\n```\n\n`concat` takes two or more lists and combines them into a single list.'}, 'range': {...}}
line    4 col   8 merge           -> {'contents': {'kind': 'markdown', 'value': '```terraform\nmerge(…maps dynamic) dynamic\n```\n\n`merge` takes an arbitrary number of maps or objects, and returns a single map or object that contains a merged set of elements from all arguments.'}, 'range': {...}}
line    9 col  19 concat          -> None
line   15 col  18 merge           -> None
```

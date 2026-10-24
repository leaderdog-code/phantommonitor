#!/usr/bin/env python3
"""
blast_radius.py — "what files are affected by a change to concept X?"

Don't hand-maintain "what each file does" (it rots, and stale notes are what make an assistant
argue from a wrong picture). Instead, ask the LIVE code: give a concept's ANCHORS (the function
names / file names / keywords that mark its footprint) and this prints every file that touches
them — computed now, never stale.

    python scripts/blast_radius.py <concept>           # built-in anchor set
    python scripts/blast_radius.py "someFunction" "some_table"   # ad-hoc anchors (regex)
    python scripts/blast_radius.py --list              # show built-in concepts
    python scripts/blast_radius.py --root D:\\other  <concept>    # scan a different project

Built-in concept anchor sets should mirror docs/SYSTEM.md's subsystem ledger — keep them in sync.

SETUP (done during bootstrap): fill in CONCEPTS below from the subsystem ledger, and set EXTS to
this project's source file extensions.
"""
import os
import re
import sys

# ── WHICH TREE TO SCAN ───────────────────────────────────────────────────────────────────────
# ⚠️ THIS SCRIPT MUST NEVER SCAN "THE PROJECT IT WAS WRITTEN FOR". It used to default to its own
# folder, so once the same kit was used on a second project it kept answering about the FIRST one —
# real files, real hit counts, wrong project, and nothing on screen to say so. A tool whose entire
# job is "tell me every file this change touches" is worse than useless when it quietly reports on
# somewhere else: it is the one tool you are least likely to double-check, because you ran it
# precisely so you would not have to.
#
# So it follows YOU: the project you are standing in, found by walking up to the nearest .git.
# Explicit wins over that, and the tree it scanned is PRINTED on every run.
#     --root <path>            most explicit
#     BLAST_ROOT=<path>        environment
#     (otherwise)              the project containing your current directory


def _project_root(start):
    """Walk up from `start` to the top of the project it belongs to.

    ⚠️ THE FOLDER YOU ARE STANDING IN IS USUALLY NOT THE PROJECT. Run this from <proj>/templates
    and scanning the cwd alone would search templates/ and cheerfully report that nothing else
    touches the concept — the same wrong-answer-that-looks-right, one level down.
    """
    d = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(d, '.git')):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.path.abspath(start)      # not in a repo: scan where you are
        d = parent


_ROOT = os.path.abspath(os.environ['BLAST_ROOT']) if os.environ.get('BLAST_ROOT') \
    else _project_root(os.getcwd())

# File types to scan. Set these to whatever this project is written in.
EXTS = ('.py', '.js', '.ts', '.jsx', '.tsx', '.html', '.css', '.go', '.rs', '.java',
        '.rb', '.php', '.cs', '.cpp', '.c', '.h', '.sql', '.json', '.yaml', '.yml')

# Directories to skip.
SKIP_DIRS = {'.git', '__pycache__', 'node_modules', 'dist', 'build', 'vendor',
             '.venv', 'venv', '.next', 'target', 'backups',
             '.claude'}   # .claude/gate_shadow is the regression gate's mirror of the code: never count it twice

# concept -> list of anchor regexes (the searchable vocabulary of that subsystem).
# FILL THIS IN during bootstrap, one entry per row of the SYSTEM.md subsystem ledger. Example:
#   'auth': [r'\blogin\b', 'session_token', 'auth_middleware', 'users.db'],
CONCEPTS = {
    # 'concept': ['anchor1', r'anchor_regex2', 'filename.ext'],
}


def search(anchors):
    pat = '|'.join(f'(?:{a})' for a in anchors)
    hits = {}
    for root, dirs, files in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if not f.endswith(EXTS):
                continue
            p = os.path.join(root, f)
            try:
                with open(p, encoding='utf-8', errors='ignore') as fh:
                    text = fh.read()
            except OSError:
                continue
            n = len(re.findall(pat, text, re.IGNORECASE))
            if n:
                hits[os.path.relpath(p, _ROOT)] = n
    return hits


def _load_doc_concepts():
    """Concepts from the cascading memory: every docs/systems/<name>.md header's `anchors:` line.
    The subsystem docs are the single source; a doc's anchors replace a built-in of the same name.
    (Same as C:\\QCT\\scripts\\blast_radius.py — change both.)"""
    sd = os.path.join(_ROOT, 'docs', 'systems')
    try:
        names = sorted(os.listdir(sd))
    except OSError:
        return
    for fn in names:
        if not fn.endswith('.md') or fn.startswith('_'):
            continue
        try:
            with open(os.path.join(sd, fn), encoding='utf-8', errors='replace') as fh:
                head = fh.read(8000)
        except OSError:
            continue
        m = re.match(r'\ufeff?---\s*\n(.*?)\n---\s*\n', head, re.S)
        if not m:
            continue
        meta = dict(l.split(':', 1) for l in m.group(1).splitlines() if ':' in l)
        meta = {k.strip().lower(): v.strip() for k, v in meta.items()}
        name = meta.get('subsystem')
        anchors = [a.strip() for a in meta.get('anchors', '').split(',') if a.strip()]
        if name and anchors:
            CONCEPTS[name] = anchors


def main():
    args = sys.argv[1:]
    if '--root' in args:
        i = args.index('--root')
        globals()['_ROOT'] = os.path.abspath(args[i + 1])
        del args[i:i + 2]
    _load_doc_concepts()
    if not args or args[0] in ('-h', '--help'):
        print(__doc__)
        return
    if args[0] == '--list':
        if not CONCEPTS:
            print("No concepts defined yet — fill in CONCEPTS from docs/SYSTEM.md's ledger.")
            return
        for k, v in CONCEPTS.items():
            print(f"  {k:14} {v}")
        return
    if len(args) == 1 and args[0] in CONCEPTS:
        anchors, label = CONCEPTS[args[0]], args[0]
    else:
        anchors, label = args, ' '.join(args)
    hits = search(anchors)
    # ⚠️ ALWAYS SAY WHICH TREE. When it scanned the wrong project the output looked perfectly
    # healthy. Naming the root turns a plausible answer into an obviously wrong one.
    print(f"\nBLAST RADIUS — concept '{label}'  ({len(anchors)} anchors)")
    print(f"tree: {_ROOT}")
    print(f"anchors: {anchors}\n")
    for f, n in sorted(hits.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5}x  {f}")
    print(f"\n=> {len(hits)} files touch this concept (live, computed now)")


if __name__ == '__main__':
    main()

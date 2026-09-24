# template-sync

A small Python CLI tool to apply reusable document template bundles from a central template repository into target working directories.

## Intention

The project is meant to replace manual copy-paste workflows when starting new documents (for example assignment submissions in Typst or LaTeX).

The core idea is:

- maintain multiple templates (consisting of any amount of Jinja2 files or "regular" files) in a central template repository
- run `template-sync` to copy/render the files of a specific template (potentialliy with parameters) into a target directory
- then you can edit the files created from the template without affecting other projects
- you can manually run `template-sync` again to compare the template files from the repository with the files in the target directory and pull updates from the repository into a target directory or merge changes to a file in a target directory into its corresponding file in the template repository

The tool is intentionally format-agnostic. It should work for Typst, LaTeX, and any other file types.

## Concrete Workflow Example

This example shows a full end-to-end setup for a Typst assignment workflow.

### 1. Create a template repository

```bash
mkdir -p ~/templates-repo/typst
cd ~/templates-repo
git init
```

Create a shared static file:

```bash
cat > typst/common.typ << 'EOF'
#set page(margin: 2cm)
#set text(font: "Libertinus Serif")
EOF
```

Create a dynamic Jinja2 file:

```bash
cat > typst/assignment.typ.j2 << 'EOF'
= {{ class_name }} - Assignment {{ sheet_number }}
Author: {{ student_name }}
Year: {{ year }}

== Task 1
Start writing here.
EOF
```

Create templates.yaml:

```bash
cat > templates.yaml << 'EOF'
version: snapshot

templates:
  assignment:
    description: Test template
    parameters:
      - name: name
    files:
      - source: templates/base.typ
        target: base.typ
        mode: static
        jinja: false
      - source: templates/doc.typ.j2
        target: main.typ
        mode: dynamic
        jinja: true
EOF
```

Optional but recommended:

```bash
git add .
git commit -m "add typst assignment template"
```

### 2. Apply the template in a target directory

```bash
mkdir -p ~/uni/physics101/sheet-01
cd ~/uni/physics101/sheet-01

template-sync apply typst-assignment \
  --repo ~/templates-repo \
  --set student_name="Alex Example" \
  --set sheet_number="01"
```

`template-sync` will ask you for the missing `class_name` (for example "Physics 101") and then generate the following file structure:

```text
sheet-01/
  common.typ
  main.typ
  .template-sync/
    state.json
```

- common.typ is copied from a template file.
- main.typ is rendered from a Jinja2 template file.
- .template-sync/state.json stores template name, parameters, source repo commit, and file metadata for future sync/update operations.

You can then start editing `main.typ`. If you need to adjust the page configuration for this project, you can do so by editing `common.typ`. This will not affect other projects.

## Current State (First Iteration)

Implemented now:

- CLI commands:
  - list: list available templates from a template repository
  - apply: apply a selected template into a target directory
- YAML template config parsing with validation
- support for static and dynamic template files
- Jinja2 rendering for dynamic files
- parameter input via:
  - direct CLI values (KEY=VALUE)
  - interactive prompts for missing values
- metadata tracking in .template-sync/state.json:
  - selected template name
  - parameter values used
  - template repository path
  - template repository commit hash (if available)
  - file-level metadata including static/dynamic mode and source checksum
- automated tests with pytest for core logic and CLI behavior

Not implemented yet:

- check if the source template changed since apply
- update static files from newer template versions
- optional regeneration of dynamic files during update
- compare target modifications against source templates and push back changes to template repository branches
- parent-directory preset configs for creating new subdirectories with partially prefilled parameters

## Template Config Shape

Template bundles are defined in a YAML file (default name: `templates.yaml`) in the template repository.

Each template includes:

- description
- parameters (simple names or objects with prompt/default/required)
- files (source, target, mode, jinja)

The mode field distinguishes:

- static: usually not edited in targets
- dynamic: intended to be edited after generation

## Quick Start

### Requirements

- Python 3.14+
- Poetry

### Install dependencies

```bash
poetry install --with dev
```

### Run tests

```bash
poetry run pytest -q
```

### Example CLI usage

```bash
template-sync list --repo /path/to/template-repo
template-sync apply typst-assignment --repo /path/to/template-repo --set class_name=Physics101 --set student_name=Alex
```

## Roadmap Goals

Planned next capabilities:

1. Template drift detection: compare target state with newer template repository versions.
2. Controlled updates: update static files by default, optionally regenerate dynamic files.
3. Upstream contribution flow: compare local static-file edits with template source and create/apply changes on a new template-repository branch.
4. Preset-based directory creation: read local JSON presets, prompt only for missing parameters, generate into a newly created subdirectory.
5. Better diff and conflict UX for update operations.

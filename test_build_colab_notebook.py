"""Tests that the generated one-click Colab run is resumable on Drive."""

from __future__ import annotations

import ast
import base64
import json
import tempfile
import unittest
from pathlib import Path

import build_colab_notebook


class NotebookBuilderTests(unittest.TestCase):
    def test_checked_in_notebook_embeds_current_postprocessors(self) -> None:
        notebook = json.loads(build_colab_notebook.NOTEBOOK.read_text(encoding="utf-8"))
        code_cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
        self.assertEqual(len(code_cells), 1)
        code = "".join(code_cells[0]["source"])
        tree = ast.parse(code)
        embedded_modules = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "embedded_modules"
                for target in node.targets
            )
        )

        for module_name in build_colab_notebook.MODULES:
            with self.subTest(module=module_name):
                embedded = base64.b64decode(embedded_modules[module_name])
                current = (build_colab_notebook.ROOT / module_name).read_bytes()
                self.assertEqual(embedded, current)

    def test_notebook_checkpoints_on_drive_and_resumes_same_incomplete_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            notebook_path = root / "run_40HD_colab.ipynb"
            notebook_path.write_text(json.dumps({
                "cells": [{"cell_type": "code", "source": ["print('starter')\n"]}],
            }), encoding="utf-8")
            for module_name in build_colab_notebook.MODULES:
                (root / module_name).write_text("# embedded test module\n", encoding="utf-8")

            old_root, old_notebook = build_colab_notebook.ROOT, build_colab_notebook.NOTEBOOK
            try:
                build_colab_notebook.ROOT = root
                build_colab_notebook.NOTEBOOK = notebook_path
                build_colab_notebook.main()
            finally:
                build_colab_notebook.ROOT, build_colab_notebook.NOTEBOOK = old_root, old_notebook

            generated = json.loads(notebook_path.read_text(encoding="utf-8"))
            code = "".join(next(c for c in generated["cells"] if c["cell_type"] == "code")["source"])

        compile(code, "<generated colab cell>", "exec")

        self.assertIn("drive_results_root = Path('/content/drive/MyDrive/SoSanhOCR_40HD/ket_qua')", code)
        self.assertIn("'--output-dir', str(drive_results)", code)
        self.assertIn("'--resume'", code)
        self.assertIn("ocr_regions.jsonl", code)
        self.assertIn("COMPLETE", code)
        self.assertIn("bundle_sha256", code)
        self.assertIn("regions_jsonl=drive_results / 'ocr_regions.jsonl'", code)


if __name__ == "__main__":
    unittest.main()

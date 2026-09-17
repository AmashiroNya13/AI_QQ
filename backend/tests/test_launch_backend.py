import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import launch_backend


class LauncherRootTests(unittest.TestCase):
    def test_defaults_to_standard_user_astrbot_root(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), patch.object(
            Path, "home", return_value=Path(directory)
        ):
            root = launch_backend.configure_astrbot_root()

        self.assertEqual(root, (Path(directory) / ".astrbot").resolve())

    def test_explicit_root_is_preserved(self):
        with TemporaryDirectory() as directory, patch.dict(
            os.environ, {"ASTRBOT_ROOT": directory}, clear=True
        ):
            root = launch_backend.configure_astrbot_root()

        self.assertEqual(root, Path(directory).resolve())

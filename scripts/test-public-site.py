"""Publication boundary tests using invented fixtures, without archive access."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('guard',Path(__file__).with_name('check-public-site.py'))
guard=importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)


class PublicationBoundary(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        (self.root/'public-docs').mkdir()
        (self.root/'public-site-files.txt').write_text('public-site-files.txt\nzensical.toml\n',encoding='utf-8')
        (self.root/'zensical.toml').write_text('[project]\ndocs_dir="public-docs"\nsite_dir="site"\n[project.theme]\nfont=false\n',encoding='utf-8')
        self.page=self.root/'public-docs/index.md'; self.page.write_text('# Synthetic public note',encoding='utf-8')
    def tearDown(self): self.temp.cleanup()
    def test_only_reviewed_inputs(self): self.assertIn('public-docs/index.md',guard.check_sources(self.root))
    def test_public_https_reference_is_allowed(self):
        self.page.write_text('[Public documentation](https://zensical.org/docs/)')
        guard.check_sources(self.root)
    def test_raw_export_rejected(self):
        (self.root/'public-docs/raw.jsonl').write_text('{}')
        with self.assertRaises(guard.PublicationError): guard.check_sources(self.root)
    def test_private_drafts_and_includes_rejected(self):
        for text in ('---\ndraft: true\n---\n# Note','--8<-- "outside.md"','C:\\Users\\Synthetic\\file','kindroid-backup-data/example','https://storage.kindroid.ai/example'):
            with self.subTest(text=text):
                self.page.write_text(text,encoding='utf-8')
                with self.assertRaises(guard.PublicationError): guard.check_sources(self.root)
    def test_credential_rejected_without_value_in_error(self):
        secret='kn_'+'x'*24; self.page.write_text(secret)
        with self.assertRaises(guard.PublicationError) as error: guard.check_sources(self.root)
        self.assertNotIn(secret,str(error.exception))
    def test_escaping_links_rejected(self):
        self.page.write_text('[outside](../../outside.md)')
        with self.assertRaises(guard.PublicationError): guard.check_sources(self.root)
    def test_configuration_cannot_widen_input(self):
        path=self.root/'zensical.toml'; path.write_text(path.read_text().replace('"public-docs"','".."'))
        with self.assertRaises(guard.PublicationError): guard.check_sources(self.root)
    def test_tooling_manifest_cannot_add_data(self):
        path=self.root/'public-site-files.txt'; path.write_text(path.read_text()+'kindroid.env\n')
        with self.assertRaises(guard.PublicationError): guard.check_sources(self.root)
    def test_generated_search_data_checked(self):
        site=self.root/'site'; site.mkdir(); (site/'index.html').write_text('<h1>Public</h1>')
        (site/'search.json').write_text('{"text":"'+('kn_'+'x'*24)+'"}')
        with self.assertRaises(guard.PublicationError): guard.check_built(self.root)


if __name__=='__main__': unittest.main(verbosity=2)

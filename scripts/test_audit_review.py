"""Regression tests for source identity and lossless comment accounting."""
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from audit_review import docx_comments, reconcile_comments


class CommentAccountingTests(unittest.TestCase):
    def test_duplicate_quotes_need_separate_opinions(self):
        comments=[dict(source_comment_id=str(i),quote='同一条意见') for i in range(2)]
        opinions=[dict(id='op-1',quote='同一条意见',target=dict(document_id='doc-new',block_ids=[]))]
        result=reconcile_comments(comments,opinions,'doc-new')
        self.assertEqual(sum(c['imported_opinion_id'] is None for c in result),1)
        self.assertEqual([c['source_comment_id'] for c in result],['0','1'])

    def test_table_and_unanchored_comments_survive(self):
        ns='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.docx'
            with ZipFile(path,'w') as z:
                z.writestr('word/document.xml',f'<w:document xmlns:w="{ns}"><w:body><w:tbl><w:tr><w:tc><w:p><w:commentRangeStart w:id="7"/><w:r><w:t>值</w:t></w:r><w:commentReference w:id="7"/></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
                z.writestr('word/comments.xml',f'<w:comments xmlns:w="{ns}"><w:comment w:id="7" w:author="源作者"><w:p><w:r><w:t>核算表值</w:t></w:r></w:p></w:comment><w:comment w:id="8"><w:p><w:r><w:t>未定位意见</w:t></w:r></w:p></w:comment></w:comments>')
            result=docx_comments(path,dict(id='doc-new'),[dict(id='table-1',source_locator='word/document.xml / body[1]')])
            self.assertEqual(len(result),2)
            self.assertEqual(result[0]['candidate_block_ids'],['table-1'])
            self.assertEqual(result[1]['quote'],'未定位意见')
            self.assertEqual(result[1]['candidate_block_ids'],[])


if __name__=='__main__':unittest.main()

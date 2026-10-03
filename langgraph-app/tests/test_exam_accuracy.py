"""Regression checks for provenance, independent reasoning and pixel evidence."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image, ImageDraw

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lg_assistant import config, exam_graph as eg, exam_pixels, llm, vision, public_knowledge as pk
from lg_assistant.ported.grids import check_grids


class AccuracyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'grid.png'
    def tearDown(self):self.tmp.cleanup()

    def test_public_knowledge_default_applies_to_api_and_graph_but_preserves_private_scope(self):
        self.assertTrue(eg.options({})['use_public_knowledge'])
        self.assertFalse(eg.options({'exam':{'use_public_knowledge':False}})['use_public_knowledge'])
        for settings in ({'profile':'internal'}, {'require_knowledge':True},
                         {'document_ids':['a'*32]}):
            self.assertFalse(eg.options({'exam':settings})['use_public_knowledge'])

    def matrix_image(self,n):
        im=Image.new('RGB',(900,780),'white');d=ImageDraw.Draw(im)
        cells={0,n-1,n*n-2};side=100;step=side/n
        positions=[(100+c*220,35+r*160) for r in range(3) for c in range(3) if (r,c)!=(2,2)]
        positions += [(30+c*210,620) for c in range(4)]
        for x,y in positions:
            for r in range(n):
                for c in range(n):
                    a=x+c*step;b=y+r*step
                    d.rectangle((a,b,a+step,b+step),outline='#999999',width=1)
                    if r*n+c in cells:d.rectangle((a+2,b+2,a+step-2,b+step-2),fill='#183043')
        im.save(self.path)
        return cells

    def test_pixel_reader_preserves_cells_at_varied_sizes(self):
        for n in (2,3,5):
            cells=self.matrix_image(n);expected=''.join('1' if i in cells else '0' for i in range(n*n))
            grids=exam_pixels.read_grids(self.path)
            self.assertEqual(len(grids),12)
            self.assertTrue(all(g['bits']==expected and g['side']==n for g in grids))
            observed=exam_pixels.observe_grids([str(self.path)])
            self.assertEqual(observed['pages'][0]['matrix_layout_candidate']['rows'][2][2],'?')

    def test_non_binary_marks_are_not_claimed_as_solid_grid_cells(self):
        im=Image.new('RGB',(160,160),'white');d=ImageDraw.Draw(im)
        for x in (20,70,120):d.line((x,20,x,120),fill='black')
        for y in (20,70,120):d.line((20,y,120,y),fill='black')
        d.ellipse((35,35,55,55),fill='black');im.save(self.path)
        self.assertEqual(exam_pixels.read_grids(self.path),[])

    def test_provider_json_generation_failure_retries_without_format_constraint(self):
        failure=llm.LLMError('Model output became abnormal while generating a JSON response')
        with patch.object(llm,'vision',side_effect=[failure,'{"answerable": true, "candidate": "B"}']) as call:
            result=vision._json_call('Return JSON object','question',[str(self.path)])
        self.assertTrue(result['answerable'])
        self.assertEqual(call.call_count,2)
        self.assertTrue(call.call_args_list[0].kwargs['json_mode'])
        self.assertFalse(call.call_args_list[1].kwargs['json_mode'])

    def test_provider_auth_failure_is_not_retried(self):
        with patch.object(llm,'vision',side_effect=llm.LLMError('Error code: 401 invalid API key')) as call:
            with self.assertRaises(llm.LLMError):vision._json_call('JSON','question',[str(self.path)])
        self.assertEqual(call.call_count,1)

    def test_optional_independent_solution_timeout_is_not_retried(self):
        failure=llm.LLMError('network timeout')
        with patch.object(llm,'vision',side_effect=failure) as call:
            with self.assertRaises(llm.LLMError):
                vision.independent_solution('看图解题',[str(self.path)])
        call.assert_called_once()
        self.assertEqual(call.call_args.kwargs['model'],config.EXAM_INDEPENDENT_MODEL)

    def test_lossless_image_encoding_and_phone_exif_orientation(self):
        im=Image.new('RGB',(80,140),'white');e=Image.Exif();e[274]=6
        path=self.path.with_suffix('.jpg');im.save(path,exif=e)
        data=llm.to_data_url(path)
        self.assertTrue(data.startswith('data:image/png;'))
        with Image.open(io.BytesIO(base64.b64decode(data.split(',')[1]))) as encoded:
            self.assertEqual(encoded.size,(140,80))

    def test_transformed_grid_rule_uses_all_known_rows(self):
        # Clockwise rotation followed by XOR: all cells, not just black counts.
        rows=[['1000','1000','1100'],['0010','0100','1100'],['0100','0010','?']]
        out=check_grids({'rows':rows,'options':{'A':'0001','B':'0011','C':'1001','D':'0110'}})
        supported=[c for c in out['candidates'] if c['left_transform']=='rotate90' and 'xor' in c['rule'] and c['direction']=='row']
        self.assertEqual(supported[0]['matching_options'],['B'])
        rows[1][2]='1110'
        bad=check_grids({'rows':rows,'options':{'A':'0001','B':'0011'}})
        self.assertFalse(any(c['left_transform']=='rotate90' and 'xor' in c['rule'] and c['direction']=='row' for c in bad['candidates']))

    def test_missing_first_grid_position_can_be_checked(self):
        out=check_grids({'rows':[['?','10','11'],['11','01','10'],['10','11','01']],
                        'options':{'A':'01','B':'10','C':'00','D':'11'}})
        self.assertTrue(any(c['matching_options']==['A'] and 'xor' in c['rule'] for c in out['candidates']))

    def test_grid_dimensions_describe_panel_not_flat_string(self):
        bits='1'*25
        result=check_grids({'rows':[[bits,bits,bits],[bits,bits,bits],[bits,bits,'?']], 'options':{'A':bits,'B':'0'*25}})
        self.assertEqual(result['cell_count_per_panel'],25)
        self.assertEqual(result['square_shape_candidate'],{'rows':5,'columns':5})

    def test_other_graphic_types_are_outside_matrix_tool_scope(self):
        result=vision.run_tools({'binary_grid':{'rows':[['101'],['010'],['111']], 'options':{'A':'010'}}})
        self.assertEqual(result['grid_checks']['status'],'not_applicable')

    def test_prose_is_not_arithmetic_but_numeric_errors_still_require_repair(self):
        result=vision.run_tools({'calculations':[{'expression':'旋转90度后 A XOR B -> 结果'},
                                                {'expression':'1/0'},{'expression':'12.3'}]})
        self.assertEqual(result['calculations_unsupported'],1)
        self.assertEqual(result['calculations_skipped'],1)
        self.assertEqual(result['calculations'][0]['expression'],'1/0')
        self.assertIn('error',result['calculations'][0])

    def test_pixel_components_use_shared_edges_not_corners(self):
        self.matrix_image(2)
        # Cells 0,1,2 share edges and form a single component.
        self.assertEqual(exam_pixels.read_grids(self.path)[0]['edge_connected_components'],1)


class KnowledgeBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'grid.png'
        self.db_patch=patch.object(config,'DB_PATH',Path(self.tmp.name)/'app.sqlite3')
        self.db_patch.start()
    def tearDown(self):self.db_patch.stop();self.tmp.cleanup()

    def test_bundle_install_is_idempotent_and_contains_no_eval_fields(self):
        catalog=pk.bundled_catalog()
        self.assertGreaterEqual(catalog['modules']['图形推理'],25)
        self.assertGreaterEqual(catalog['modules']['类比推理'],20)
        self.assertEqual(pk.ensure_bundled(),catalog['count'])
        self.assertEqual(pk.ensure_bundled(),0)
        db=pk.connect()
        try:
            self.assertEqual(db.execute('SELECT count(*) FROM public_concepts').fetchone()[0],catalog['count'])
        finally:db.close()
        rows=json.loads((pk.BUNDLE_DIR/'exam_concepts.json').read_text(encoding='utf-8'))
        forbidden={'answer','answers','expected','question','gold','rubric','rubrics'}
        self.assertTrue(all(not forbidden.intersection(r) for r in rows))

    def test_specific_topic_matches_and_unrelated_generic_query_does_not(self):
        pk.ensure_bundled()
        for text,kind,topic in [('图形旋转叠加异或九宫格','reasoning','叠加'),
                               ('类比推理中的种属关系','reasoning','种属'),
                               ('资料分析求基期与同比增长率','quantitative','基期'),
                               ('外键与检查约束','knowledge','外键')]:
            refs=pk.retrieve(text,kind)
            self.assertTrue(any(topic in r['title'] for r in refs),[r['title'] for r in refs])
            self.assertLessEqual(len(refs),4)
        self.assertFalse(pk.retrieve('请给出正确答案','knowledge'))

    def test_methods_are_retrieved_without_hypothetical_web_answers(self):
        state={'options':eg.options({'exam':{'use_public_knowledge':True}}),'owner':'test','specialist':'reasoning',
               'observation':'题型：图形推理。折纸打孔后沿折痕展开，核对孔位。','text':'解题'}
        with patch.object(eg.search,'search_answer') as search:
            result=eg.retrieve(state)
        self.assertTrue(any('折纸' in r['title'] for r in result['references']))
        search.assert_not_called()

    def test_law_summary_needs_date_aware_verification(self):
        state={'options':eg.options({'exam':{'use_public_knowledge':True,'allow_web':False}}),'owner':'test','specialist':'knowledge',
               'observation':'根据民法典，民事法律行为的意思表示条件是什么？','text':'解题'}
        result=eg.retrieve(state)
        self.assertIn('时效',result['evidence_missing'])
        state['options']['allow_web']=True
        with patch.object(eg.search,'search_answer',return_value={'answer':'按题干年份核实的原文','sources':[{'url':'https://example.org/law'}]}) as search:
            result=eg.retrieve(state)
        search.assert_called_once()
        self.assertTrue(result['sources'])
        self.assertIn('按题干年份',result['evidence'])

    def test_selected_private_document_does_not_import_or_use_public_bundle(self):
        state={'options':eg.options({'exam':{'profile':'internal'}}),'owner':'test','specialist':'knowledge',
               'observation':'单位内部请假制度','text':'解题'}
        with patch.object(pk,'ensure_bundled') as install,patch.object(pk,'retrieve') as retrieve:
            result=eg.retrieve(state)
        install.assert_not_called();retrieve.assert_not_called()
        self.assertIn('evidence_missing',result)

    def state(self):
        return {'draft':{'candidate':'B'},'tools':{},'images':[],'evidence':'辅助概念',
                'references':[{'id':'r1','content':'这是可用于辅助理解的公共知识原文。'}],
                'options':eg.options({}),'evidence_missing':''}

    def test_optional_concept_does_not_block_self_contained_answer(self):
        s=self.state()
        with patch.object(vision,'review',return_value={'answerable':True,'answer':'B','citations':[]}):
            result=eg.review(s)
        self.assertTrue(result['final']['answerable'])

    def test_repair_and_disagreement_use_stronger_reviewer(self):
        s=self.state();s['repair_count']=1
        with patch.object(vision,'review',return_value={'answerable':True,'answer':'B','repair_issues':[]}) as review:
            result=eg.review(s)
        self.assertEqual(review.call_args.kwargs['model'],config.EXAM_REVIEW_MODEL)
        self.assertEqual(result['review_model'],config.EXAM_REVIEW_MODEL)
        s['repair_count']=0;s['draft']={'answerable':True,'candidate':'B'}
        s['independent_draft']={'answerable':True,'candidate':'C'}
        with patch.object(vision,'review',return_value={'answerable':True,'answer':'B','repair_issues':[]}) as review:
            eg.review(s)
        self.assertEqual(review.call_args.kwargs['model'],config.EXAM_REVIEW_MODEL)

    def test_corrected_arithmetic_is_actually_recomputed(self):
        s=self.state();s['tools']={'calculations':[{'expression':'1/0','error':'除数为0'}]}
        final={'answerable':True,'answer':'B','repair_issues':[],
               'calculation_corrections':[{'original_expression':'1/0','expression':'8/2','reason':'原图分子是8，分母是2'}]}
        with patch.object(vision,'review',return_value=final):result=eg.review(s)
        self.assertTrue(result['final']['answerable'])
        self.assertEqual(result['tools']['calculations'][0]['result'],4)
        self.assertEqual(result['tools']['calculations'][0]['original_expression'],'1/0')

    def test_plotted_data_is_reviewed_by_stronger_reader_before_conflict(self):
        s=self.state();s.update(specialist='quantitative',observation='折线图中比较两条曲线在某年的值。')
        with patch.object(vision,'review',return_value={'answerable':True,'answer':'B','repair_issues':[]}) as review:
            eg.review(s)
        self.assertEqual(review.call_args.kwargs['model'],config.EXAM_REVIEW_MODEL)

    def test_unrelated_or_noncomputed_correction_cannot_hide_error(self):
        for old,new in [('irrelevant','2+2'),('1/0','4'),('1/0','1/0')]:
            s=self.state();s['tools']={'calculations':[{'expression':'1/0','error':'除数为0'}]}
            final={'answerable':True,'answer':'B','repair_issues':[],
                   'calculation_corrections':[{'original_expression':old,'expression':new,'reason':'test'}]}
            with patch.object(vision,'review',return_value=final):result=eg.review(s)
            self.assertFalse(result['final']['answerable'])
            self.assertIn('error',result['tools']['calculations'][0])

    def test_invalid_optional_citation_is_never_displayed(self):
        s=self.state()
        with patch.object(vision,'review',return_value={'answerable':True,'answer':'B','citations':[{'id':'r1','quote':'虚构原文'}]}):
            result=eg.review(s)
        self.assertEqual(result['final']['citations'],[])
        self.assertTrue(result['final']['answerable'])

    def test_required_source_still_blocks_forged_quote(self):
        s=self.state();s['options']=eg.options({'exam':{'profile':'internal'}})
        with patch.object(vision,'review',return_value={'answerable':True,'answer':'B','citations':[{'id':'r1','quote':'虚构原文'}]}):
            result=eg.review(s)
        self.assertFalse(result['final']['answerable'])
        self.assertEqual(result['final']['answer'],'')

    def test_conditional_logic_does_not_search_for_hypothetical_policy(self):
        s={'options':eg.options({}),'owner':'test','specialist':'reasoning',
           'observation':'假设最新政策实施后，若甲则乙，以下哪项削弱论证？','text':'解题'}
        with patch.object(eg.knowledge,'retrieve',return_value=[]),patch.object(eg.search,'search_answer') as search:
            result=eg.retrieve(s)
        self.assertNotIn('evidence_missing',result)
        self.assertTrue(all(r['specialist'] in ('reasoning','all') for r in result.get('references',[])))
        search.assert_not_called()

    def test_independent_call_never_receives_primary_answer(self):
        s={'text':'看图解题','options':eg.options({}),'images':[str(self.path)],'specialist':'reasoning',
           'draft':{'candidate':'PRIMARY_SECRET'},'tools':{},'observation':'OCR_SECRET','pixel_observation':{}}
        with patch.object(vision,'independent_solution',return_value={'answerable':True,'candidate':'C'}) as independent:
            result=eg.independent_reasoning(s)
        self.assertNotIn('PRIMARY_SECRET',str(independent.call_args))
        self.assertNotIn('OCR_SECRET',str(independent.call_args))
        self.assertEqual(result['independent_draft']['candidate'],'C')

    def test_secondary_outage_is_recorded_without_discarding_primary(self):
        s={'text':'解题','options':eg.options({}),'images':[],'specialist':'reasoning','tools':{}}
        with patch.object(vision,'independent_solution',side_effect=llm.LLMError('network timeout')):
            result=eg.independent_reasoning(s)
        self.assertNotIn('error',result)
        self.assertIn('network timeout',result['independent_error'])
        self.assertEqual(result['independent_draft'],{})


if __name__=='__main__':unittest.main(verbosity=2)

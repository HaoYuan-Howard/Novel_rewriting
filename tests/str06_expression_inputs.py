"""R9 合成样例的显式表达输入；不迁移生产数据，不替模型推断语义。"""
from contracts.schema.expression import ExpressionRequirement, ExpressionReadings, SourceExpression
from contracts.schema.hook import HookPov


def sample_narration():
    return HookPov(person='third', focalizers=('narrator',))


def sample_requirements():
    return (ExpressionRequirement(ex_id='ex_01', kind='narration', statement='第三人称旁白叙述。'),)


def sample_book_expression(characters):
    refs = next((s['source_refs'] for c in characters.values() for s in c.get('surfaces', ())
                 if isinstance(s, dict) and s.get('source_refs')), ['e01_01'])
    return {'expression': {'narration': {'person': 'third', 'focalizers': ['narrator'],
                                        'statement': '第三人称旁白叙述。', 'source_refs': list(refs)},
                           'mechanisms': []}}


def sample_readings():
    return ExpressionReadings(status='unavailable', paragraph_mean_chars=None,
                              sentence_mean_chars=None, dialogue_ratio=None)


def sample_blueprint_expression():
    return SourceExpression(narration=sample_narration(), readings=sample_readings(),
                            items=({'ex_id': 'ex_01', 'kind': 'narration',
                                    'statement': '第三人称旁白叙述。', 'source_refs': ['e01_01']},))


def sample_design_expression(scenes):
    def pov(scene):
        if isinstance(scene, str):
            return scene
        return scene.get('delivery', {}).get('pov') if isinstance(scene, dict) else scene.delivery.pov
    povs = sorted({pov(s) for s in scenes if isinstance(pov(s), str)})
    return {'narration': {'person': 'third', 'focalizers': povs or ['narrator']},
            'expression_decisions': [{'ex_id': 'ex_01', 'action': 'change',
                                     'reason': '合成样例限定本场第三人称视角。',
                                     'target_statement': '第三人称限知叙述。'}]}


def sample_canonical_expression(scenes):
    wire = sample_design_expression(scenes)
    wire['expression_decisions'] = [{'ex_id': 'ex_01', 'action': 'change',
                                    'reason': '合成样例限定本场第三人称视角。',
                                    'statement': '第三人称限知叙述。'}]
    return wire


def sample_brief_expression():
    return {'source_narration': sample_narration(), 'expression': sample_requirements(),
            'expression_readings': sample_readings()}

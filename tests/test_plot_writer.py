"""完整节点的分场写作：字面约束按端点检查，语义完整不能被词面探针证明。"""
from types import SimpleNamespace
from novel.stages.stage5.plot_checks import fidelity_scenes, knowledge_position_findings, delivery_surface_findings


def test_sparse_literal_is_checked_only_at_registered_scene():
    entry = {'constraints': [
        {'constraint_ref': 'c01_01', 'kind': 'literal', 'value': '暮山令'},
        {'constraint_ref': 'c01_02', 'kind': 'value', 'value': '二十两'},
    ], 'scenes': [
        {'slot': 's01', 'constraint_refs': []},
        {'slot': 's02', 'constraint_refs': ['c01_01', 'c01_02']},
    ]}
    tasks = fidelity_scenes(entry)
    assert tasks[0]['verbatim'] == []
    assert tasks[1]['verbatim'] == ['暮山令']
    assert tasks[1]['keep_value'] == ['二十两']
    assert 'verbatim' not in entry['scenes'][1]


def test_registered_reveal_slot_produces_semantic_attention_not_false_truth_verdict():
    secret = '裴书远其实就是她失散的父亲'
    entry = {'output_ordinal': 1, 'knowledge_events': [{
        'kind': 'secret_reveal', 'subject_ref': {'kind': 'secret', 'ref': 'secret_01'},
        'allowed_slots': ['s02'],
    }]}
    book = SimpleNamespace(secrets=[{'secret_id': 'secret_01', 'fact': secret}])
    early = knowledge_position_findings([
        {'slot': 's01', 'text': secret + '。'}, {'slot': 's02', 'text': '后来才明白。'}], entry, book)
    assert len(early) == 1 and early[0].rule == 'knowledge_position_unver'
    assert early[0].evidence[0] == 's01'
    on_time = knowledge_position_findings([
        {'slot': 's01', 'text': '她还不认识来人。'}, {'slot': 's02', 'text': secret + '。'}], entry, book)
    assert '不能证明' in on_time[0].detail


def test_parent_surface_words_do_not_certify_each_delivery_complete():
    entry = {'output_ordinal': 1, 'beats': [
        {'beat_ref': 'b01_01', 'content': '她因对方富有且好看而施救。'}]}
    result = delivery_surface_findings([
        {'slot': 's01', 'text': '她看见了他。'}, {'slot': 's02', 'text': '她救了他。'}], entry, None)
    assert result[0].rule == 'fact_landing_unver'
    assert result[0].evidence == ['b01_01']

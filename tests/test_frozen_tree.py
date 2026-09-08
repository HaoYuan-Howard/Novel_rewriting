"""复用准备不能让用例共享可变文件，或覆盖已有业务状态。"""
import pytest

from tests.frozen_tree import FrozenTree


def test_copies_preserve_bytes_and_empty_directories_without_sharing_mutations(tmp_path):
    source = tmp_path / 'source'
    (source / 'empty').mkdir(parents=True)
    (source / 'ledger.json').write_bytes(b'{"used": 1}\r\n')
    frozen = FrozenTree.capture(source)
    first, second = tmp_path / 'first', tmp_path / 'second'
    frozen.restore(first)
    frozen.restore(second)
    assert FrozenTree.capture(first) == frozen == FrozenTree.capture(second)
    (first / 'ledger.json').write_bytes(b'{"used": 4}')
    (source / 'ledger.json').unlink()
    assert (second / 'ledger.json').read_bytes() == b'{"used": 1}\r\n'
    third = tmp_path / 'third'
    frozen.restore(third)
    assert FrozenTree.capture(third) == frozen


def test_restore_refuses_existing_state_before_writing(tmp_path):
    (tmp_path / 'original').write_bytes(b'keep')
    frozen = FrozenTree((), (('replacement', b'new'),))
    with pytest.raises(ValueError, match='空目录'):
        frozen.restore(tmp_path)
    assert list(tmp_path.iterdir()) == [tmp_path / 'original']
    assert (tmp_path / 'original').read_bytes() == b'keep'

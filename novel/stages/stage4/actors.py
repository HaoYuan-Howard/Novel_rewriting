"""呈现身份的私有解析器；公开目录不携可反查共同实体的分组或映射。

不同呈现默认保持独立 token。两个绑定各有本章开始前已公开归属同一实体的
登记称谓（``identity_revealed_from`` 章级揭示字段）时，才归一为同一公共呈现。
"""
from __future__ import annotations

import hashlib
import hmac
import json

from contracts.schema.plot import ActorRef, PlotBlueprint, PlotSubjectRef
from novel.errors import RefusedByStructure
from novel.core.text import norm
from novel.stages.stage2.character_refs import null_binding_conflict
from novel.stages.stage2.plot_compile import available_actor_bindings


def _payload(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value


def subject_key(value) -> tuple[str, ...]:
    return PlotSubjectRef.model_validate(_payload(value)).key()


def actual_subject_uses(subject, available, *, require_relation: bool = False) -> list[PlotSubjectRef]:
    """知识义务只标记底稿；关系呈现从实际 BP/Plan 使用位置取完整闭包。

不能把义务中的默认 ActorRef 当作一次表面身份决定。多个已明确的呈现全部保留；
调用者确需陈述关系却没有 use 时，交原 owner 免费补齐。
    """
    source = PlotSubjectRef.model_validate(_payload(subject))
    if source.kind != 'relation':
        return [source]
    rows = {}
    for value in available:
        ref = PlotSubjectRef.model_validate(_payload(value))
        if ref.raw_subject_key() == source.raw_subject_key():
            rows[ref.key()] = ref
    if not rows and require_relation:
        raise RefusedByStructure('知识关系缺少实际表面身份 use；须由 Blueprint/Plan owner 订正')
    return [rows[key] for key in sorted(rows)]


class ActorCatalog:
    def __init__(self, characters: dict, blueprints: dict, *, chapter_ordinal: int,
                 source_text: str, seed: str):
        if not source_text:
            raise RefusedByStructure('人物呈现解析缺少截至使用章的真实源文，不能回退空候选')
        self.characters = characters['characters']
        self.ordinal = chapter_ordinal
        self.source_text = source_text
        self.seed = seed.encode('utf-8')
        self.bindings = {}
        self.binding_origins = {}
        self.states = {}
        chapters = []
        for bp in blueprints['blueprints']:
            if bp['chapter_ordinal'] > chapter_ordinal:
                continue
            chapters.append(PlotBlueprint.model_validate(bp))
            for binding in bp['actor_bindings']:
                key = binding['presentation_id']
                if key in self.bindings and self.bindings[key] != binding:
                    raise RefusedByStructure('同一表面身份在不同章的定义冲突')
                self.bindings[key] = binding
                self.binding_origins.setdefault(key, bp['chapter_ordinal'])
            self.states.update({s['state_id']: s for s in bp['states']})
        try:
            self.available_bindings = {key: value.model_dump(mode='json')
                for key, value in available_actor_bindings(chapters, chapter_ordinal).items()}
        except ValueError as exc:
            raise RefusedByStructure(str(exc)) from exc

    def attested_aliases(self, char_id: str) -> list[dict]:
        text = norm(self.source_text)
        return [row for row in self.characters[char_id]['aliases'] if norm(row['surface']) in text]

    def resolve(self, value) -> ActorRef:
        actor = ActorRef.model_validate(_payload(value))
        if actor.char_id not in self.characters:
            raise RefusedByStructure('呈现引用不存在的人物；须由人物 owner 订正')
        if actor.presentation_ref is not None:
            binding = self.available_bindings.get(actor.presentation_ref)
            if binding is None or binding['char_id'] != actor.char_id:
                raise RefusedByStructure('呈现引用未在本章提供、缺失或归属冲突；不能回退 canonical')
            return actor
        matches = [b for b in self.available_bindings.values() if b['char_id'] == actor.char_id]
        if len(matches) > 1:
            # 同 surfaces 的携入/本章绑定视为同一公开身份；异 surfaces 才必须显式句柄。
            surface_sets = {tuple(sorted(b['surfaces'])) for b in matches}
            if len(surface_sets) > 1:
                raise RefusedByStructure('人物存在多个表面身份，当前位置必须明确 presentation_ref')
            matches = [min(matches, key=lambda b: (
                self.binding_origins.get(b['presentation_id'], self.ordinal),
                b['presentation_id']))]
        names = self.attested_aliases(actor.char_id)
        if matches:
            if null_binding_conflict(names, matches[0]['surfaces'], chapter_ordinal=self.ordinal):
                raise RefusedByStructure('唯一绑定与既有公开身份仍有歧义；须明确旧/新 presentation_ref')
            return ActorRef(char_id=actor.char_id, presentation_ref=matches[0]['presentation_id'])
        if len(names) > 1 and any(row['identity_revealed_from'] >= self.ordinal for row in names):
            raise RefusedByStructure('本章或未来才公开的多称谓映射必须明确表面身份，不能章首合并')
        return actor

    def token(self, value, *, public_catalog=None) -> str:
        actor = self.resolve(value)
        public = public_catalog or self
        presentation = actor.presentation_ref
        if presentation:
            # 两边均须有此前已公开的登记映射，不按内部 char_id 合并秘密身份。
            # 历史引用仍在其拥有章 resolve；公开 token 可使用当前章之前的已知等同。
            aliases = {r['surface'] for r in public.characters[actor.char_id]['aliases']
                       if r['identity_revealed_from'] < public.ordinal}
            candidates = {key: set(row['surfaces']) & aliases for key, row in public.bindings.items()
                          if row['char_id'] == actor.char_id}
            if candidates.get(presentation):
                presentation = min((key for key, names in candidates.items() if names),
                    key=lambda key: (public.binding_origins[key], int(key.split('_')[-1])))
        identity = ('presentation', presentation) if presentation else (
            'public_entity', actor.char_id)
        return 'actor_' + hmac.new(self.seed, json.dumps(identity).encode(), hashlib.sha256).hexdigest()[:24]

    def describe(self, value, *, allowed_slots: list[str], public_states: dict[str, list[str]]) -> dict:
        actor = self.resolve(value)
        character = self.characters[actor.char_id]
        binding = self.bindings.get(actor.presentation_ref)
        aliases = {row['surface']: row for row in character['aliases']}
        if binding is not None:
            surfaces = binding['surfaces']
            if any(surface not in aliases for surface in surfaces):
                raise RefusedByStructure('表面身份包含未登记称谓；保留候选交人物 owner 订正')
            if len(surfaces) > 1 and any(aliases[s]['identity_revealed_from'] > self.ordinal for s in surfaces):
                raise RefusedByStructure('未揭示的多个称谓不能被同一表面身份目录提前合并')
            # 映射尚未揭示也能出现这个表面称谓；公开状态才是其角色描述的来源。
            state_ref = binding['public_state_ref']
            role = self.states[state_ref]['content'] if state_ref in public_states else None
            role_slots = [s for s in allowed_slots if s in public_states.get(state_ref, [])]
        else:
            surfaces = [row['surface'] for row in self.attested_aliases(actor.char_id)
                        if row['identity_revealed_from'] <= self.ordinal]
            if not surfaces:
                # 与盲设计材料一致：first_chapter 已到、登记名尚未公开（典型第一人称）
                # 时 surfaces 可为空；未到出场章仍关闭式拒绝，禁止回退 canonical。
                if character['first_chapter'] > self.ordinal:
                    raise RefusedByStructure('人物没有当前可辨认的公开呈现；不得回退 canonical')
            role = character['role'] if character['first_chapter'] <= self.ordinal else None
            role_slots = allowed_slots if role is not None else []
        if role is None or not role_slots:
            role, role_slots = None, []
        display = surfaces[0] if surfaces else ''
        return {'actor_ref': self.token(actor), 'display_name': display,
                'surfaces': surfaces, 'surface_allowed_slots': {s: list(allowed_slots) for s in surfaces},
                'role': role, 'role_allowed_slots': role_slots,
                'allowed_slots': allowed_slots}

    def subject(self, value, *, public_catalog=None) -> dict:
        ref = PlotSubjectRef.model_validate(_payload(value))
        if ref.kind == 'character':
            return {'kind': ref.kind, 'ref': self.token(ref.actor, public_catalog=public_catalog)}
        if ref.kind in ('beat', 'state', 'constraint', 'chapter_boundary'):
            return {'kind': ref.kind, 'ref': ref.ref}
        # 同一关系底稿的不同呈现不能通过相同 rel_ID 向写手泄露共同身份。
        key = list(ref.key())
        if ref.relation_actors:
            key = [ref.kind, ref.ref, self.token(ref.relation_actors.subject, public_catalog=public_catalog),
                   self.token(ref.relation_actors.object, public_catalog=public_catalog)]
        token = hmac.new(self.seed, json.dumps(key).encode(), hashlib.sha256).hexdigest()[:24]
        return {'kind': ref.kind, 'ref': 'subject_' + token}

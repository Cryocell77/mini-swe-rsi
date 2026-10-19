"""Review cache records bound to model, parent version and source identity."""
import json


class ReviewCache:
    def __init__(self):
        self.entries = {}

    @staticmethod
    def key(context, specialisation):
        return json.dumps({**context, 'reviewer_specialisation_name':specialisation},
                          ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    def entry(self, current, proposal, context, specialisation, submission,
                               role_identity, source_execution_hash,
                               parent_version=None, model=None, require_current=True):
        stored_parent = current['parent_version'] if parent_version is None else parent_version
        stored_model = current['model'] if model is None else model
        if (require_current and (source_execution_hash != current['source_execution_hash']
                or stored_parent != current['parent_version']
                or stored_model != current['model'])):
            return None
        cache_context = {'proposal': proposal, 'context': context,
                         'parent_version': stored_parent, 'model': stored_model,
                         'source_execution_hash': source_execution_hash}
        return {'cache_key': self.key(cache_context, specialisation),
                'proposal': proposal, 'context': context,
                'reviewer_specialisation_name': specialisation,
                'submission': submission, 'role_identity': role_identity,
                'source_execution_hash': source_execution_hash,
                'parent_version': stored_parent, 'model': stored_model}

    def restore(self, seeds, current):
        if seeds is None:
            return
        if not isinstance(seeds, list):
            raise ValueError('Resume committee cache seed must be a list')
        for seed in seeds:
            if not isinstance(seed, dict):
                raise ValueError('Resume committee cache seed is malformed')
            required = {'context', 'proposal', 'reviewer_specialisation_name',
                        'submission', 'role_identity', 'source_execution_hash'}
            if not required.issubset(seed):
                raise ValueError('Resume committee cache seed is incomplete')
            if (not isinstance(seed['context'], str) or not isinstance(seed['proposal'], str)
                    or not isinstance(seed['reviewer_specialisation_name'], str)
                    or not isinstance(seed['submission'], str)
                    or not isinstance(seed['role_identity'], str) or not seed['role_identity']
                    or not isinstance(seed['source_execution_hash'], str)):
                raise ValueError('Resume committee cache seed has invalid types')
            entry = self.entry(current, seed['proposal'], seed['context'],
                                                seed['reviewer_specialisation_name'],
                                                seed['submission'], seed['role_identity'],
                                                seed['source_execution_hash'],
                                                seed.get('parent_version'), seed.get('model'),
                                                require_current=False)
            self.entries[entry['cache_key']] = entry

    def get(self, context, specialisation):
        if all(value is not None for value in context.values()):
            entry = self.entries.get(self.key(context, specialisation))
            if isinstance(entry, dict):
                return entry
        return None

    def put(self, entry):
        self.entries[entry['cache_key']] = entry

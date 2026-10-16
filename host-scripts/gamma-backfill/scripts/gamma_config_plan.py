#!/usr/bin/env python3
"""
Dry-run plan for gamma_config_import.sh: compare a gamma configuration export (gamma_config_export.sh, e.g.
from dev) with the target box's current state (captured read-only by the import) and say what an apply would
do, or why it can't. Exit status 2 = blocked.

Blockers: a config column the target lacks (sync the target's gamma version first); config rows already on
the target (the import only fills an empty configuration); an event type the source's rules use that the
target lacks. Reported: rows to insert, how rules' event configurations map (by event type name), per-event
point values that differ (COPY_AWARDS=1 would set the target to the source's), image files to copy.
Usage: gamma_config_plan.py EXPORT_DIR TARGET_STATE_DIR
"""
import collections
import csv
import os
import sys

CONFIG = ('badges_badge', 'badges_badge_rules', 'rules_rule', 'statuses_status')
EVENTS = ('events_eventtype', 'events_eventconfiguration')


def read(path):
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE))


def columns(path):
    out = collections.defaultdict(set)
    for line in open(path, encoding='utf-8'):
        if line.strip():
            table, column = line.rstrip('\n').split('\t')
            out[table].add(column)
    return out


def main():
    src, tgt = sys.argv[1], sys.argv[2]
    S = lambda name: read(os.path.join(src, name + '.tsv'))
    T = lambda name: read(os.path.join(tgt, name + '.tsv'))
    blockers, out = [], []
    w = out.append
    source = open(os.path.join(src, 'source.txt')).read().split()
    w('source: gamma %s, exported %s %s UTC' % (source[1], source[2], source[3]))

    src_cols, tgt_cols = columns(os.path.join(src, 'columns.tsv')), columns(os.path.join(tgt, 'columns.tsv'))
    for table in CONFIG + EVENTS:
        missing, extra = sorted(src_cols[table] - tgt_cols[table]), sorted(tgt_cols[table] - src_cols[table])
        if missing:
            blockers.append('target %s lacks columns %s: sync its gamma version to the source first' % (table, missing))
        if extra:
            w('note: target %s has extra columns %s (left to their defaults)' % (table, extra))

    counts = {r['table']: int(r['rows']) for r in T('counts')}
    for table in CONFIG:
        if counts.get(table):
            blockers.append('target already has %d rows in %s: the import only fills an empty configuration' % (counts[table], table))
    w('target: %s' % ', '.join('%s %d' % kv for kv in sorted(counts.items())))

    src_types = {r['id']: r['name'] for r in S('events_eventtype')}
    tgt_types = {r['name']: r['id'] for r in T('events_eventtype')}
    tgt_conf = {r['event_type_id']: r for r in T('events_eventconfiguration')}
    conf_map, same = {}, 0
    for c in S('events_eventconfiguration'):
        name = src_types[c['event_type_id']]
        target = tgt_conf.get(tgt_types.get(name, ''))
        if target is not None:
            conf_map[c['id']] = (target['id'], name, c['award'], target['award'])
            same += c['id'] == target['id']
    rules = S('rules_rule')
    src_conf_type = {c['id']: src_types[c['event_type_id']] for c in S('events_eventconfiguration')}
    unmapped = sorted({src_conf_type.get(r['event_configuration_id'], r['event_configuration_id']) for r in rules
                       if r['event_configuration_id'] not in conf_map and r['event_configuration_id'] != 'NULL'})
    if unmapped:
        blockers.append('target lacks the event types %s that source rules use' % unmapped)
    w('event configurations: %d map by event type name (%d keep the same id)' % (len(conf_map), same))

    badges, links, statuses = S('badges_badge'), S('badges_badge_rules'), S('statuses_status')
    by_cat = collections.Counter((b['category'], b['is_active'] == '1') for b in badges)
    w('would insert: rules %d, badges %d (%d active), badge-rule links %d, statuses %d' % (
        len(rules), len(badges), sum(b['is_active'] == '1' for b in badges), len(links), len(statuses)))
    for (cat, active), n in sorted(by_cat.items()):
        w('  %-58s %3d %s' % (cat, n, 'active' if active else 'inactive'))
    per_type = collections.Counter(conf_map.get(r['event_configuration_id'], (0, '?'))[1] for r in rules)
    w('rules by event type: %s' % ', '.join('%s %d' % kv for kv in per_type.most_common()))

    diffs = [(name, s, t) for _, name, s, t in conf_map.values() if s != t]
    w('per-event points that differ (source -> target now; COPY_AWARDS=1 sets the source values, default keeps the target\'s): %d' % len(diffs))
    for name, s, t in sorted(diffs):
        w('  %-40s source %4s   target %4s' % (name, s, t))

    files = [l.strip() for l in open(os.path.join(src, 'media-files.txt')) if l.strip()]
    present = {l.strip() for l in open(os.path.join(tgt, 'media-existing.txt')) if l.strip()}
    w('image files to copy: %d (%.0f MB tgz); already on the target: %d' % (
        len(files), os.path.getsize(os.path.join(src, 'media.tgz')) / 1e6, sum(f in present for f in files)))
    w('BLOCKED:\n  ' + '\n  '.join(blockers) if blockers else 'READY: an apply would do the above')
    print('\n'.join(out))
    sys.exit(2 if blockers else 0)


if __name__ == '__main__':
    main()

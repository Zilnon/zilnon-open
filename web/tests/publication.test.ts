import assert from 'node:assert/strict';
import { test } from 'node:test';
import { publication, validatePublication, date, calendarDate } from '../src/lib/publication.ts';

test('accepts the synthetic publication with all research types and watchlist companies', () => {
  assert.equal(validatePublication(publication), publication);
  assert.equal(new Set(publication.reports.map(report => report.kind)).size, 3);
  assert.ok(publication.watchlist.every(company => publication.reports.some(report => report.symbols.includes(company.symbol))));
});
test('rejects private fields and unresolved source references at the rendering boundary', () => {
  const extra = structuredClone(publication) as unknown as Record<string, unknown>;
  extra.internal_notes = 'not public';
  assert.throws(() => validatePublication(extra), /root fields/);
  const references = structuredClone(publication);
  references.reports[0].sections[0].paragraphs[0].source_ids = ['missing'];
  assert.throws(() => validatePublication(references), /unknown source reference/);
});
test('rejects unsafe source URLs and report routes', () => {
  const unsafe = structuredClone(publication);
  unsafe.reports[0].sources[0].url = 'javascript:alert(1)';
  assert.throws(() => validatePublication(unsafe), /source URL/);
  unsafe.reports[0].id = '../private';
  assert.throws(() => validatePublication(unsafe), /report id/);
});
test('preserves supported producer offsets and citation identifiers', () => {
  const input = structuredClone(publication);
  const report = input.reports[0];
  report.as_of = report.as_of.replace('Z', '+00:00');
  report.summary = '';
  const oldId = report.sources[0].id;
  report.sources[0].id = 'synthetic:sec:context';
  report.sections.forEach(section => section.paragraphs.forEach(paragraph => {
    paragraph.source_ids = paragraph.source_ids.map(id => id === oldId ? report.sources[0].id : id);
  }));
  assert.equal(validatePublication(input), input);
  assert.match(date('2026-11-09T00:15:00Z', true), /08:15 SGT$/);
});
test('rejects a generated time before the evidence cutoff', () => {
  const input = structuredClone(publication);
  input.reports[0].generated_at = '2000-01-01T00:00:00Z';
  assert.throws(() => validatePublication(input), /timestamp order/);
});

test('preserves date-only sources without inventing a timestamp', () => {
  const input = structuredClone(publication);
  const source = input.reports[0].sources[0];
  source.published_at = null;
  source.published_date = '2026-11-09';
  source.publication_precision = 'date';
  assert.equal(validatePublication(input), input);
  assert.equal(calendarDate(source.published_date), '9 Nov 2026');
  source.published_at = '2026-11-09T00:00:00Z';
  assert.throws(() => validatePublication(input), /source date precision/);
  source.published_at = null;
  source.published_date = '2026-02-30';
  assert.throws(() => validatePublication(input), /source published_date/);
});

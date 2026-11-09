import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

export type Kind = 'fundamental' | 'news' | 'social';
export type ParagraphKind = 'source_fact' | 'source_claim' | 'interpretation' | 'uncertainty' | 'research_question' | 'summary';
export interface Source { id: string; title: string; url: string; publisher: string; published_at: string | null; published_date: string | null; publication_precision: 'date' | 'timestamp' | null }
export interface Report {
  id: string; kind: Kind; title: string; symbols: string[];
  as_of: string; generated_at: string; published_at: string;
  status: 'complete'; review_status: 'unreviewed'; demo: boolean; summary: string;
  sections: { heading: string; paragraphs: { text: string; kind: ParagraphKind; source_ids: string[] }[] }[];
  metrics: { label: string; value: string; unit: string; period: string; source_ids: string[] }[];
  sources: Source[]; limitations: string[];
}
export interface Publication { schema_version: '1.0.0'; published_at: string; watchlist: { symbol: string; name: string }[]; reports: Report[] }
const kinds: Kind[] = ['fundamental', 'news', 'social'];
const paragraphKinds: ParagraphKind[] = ['source_fact', 'source_claim', 'interpretation', 'uncertainty', 'research_question', 'summary'];
function fail(path: string): never { throw new Error(`Invalid public publication: ${path}`); }
function object(value: unknown, keys: string[], path: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(path);
  const result = value as Record<string, unknown>;
  if (Object.keys(result).length !== keys.length || keys.some(key => !(key in result))) fail(`${path} fields`);
  return result;
}
function string(value: unknown, path: string): asserts value is string { if (typeof value !== 'string' || !value.trim()) fail(path); }
function array(value: unknown, path: string): unknown[] { if (!Array.isArray(value)) fail(path); return value; }
function timestamp(value: unknown, path: string) {
  string(value, path);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) fail(path);
}
export function validatePublication(value: unknown): Publication {
  const root = object(value, ['schema_version', 'published_at', 'watchlist', 'reports'], 'root');
  if (root.schema_version !== '1.0.0') fail('schema_version');
  timestamp(root.published_at, 'published_at');
  const symbols = new Set<string>();
  for (const entry of array(root.watchlist, 'watchlist')) {
    const company = object(entry, ['symbol', 'name'], 'watchlist entry');
    string(company.symbol, 'symbol'); string(company.name, 'company name');
    if (!/^[A-Z][A-Z0-9.-]{0,9}$/.test(company.symbol) || symbols.has(company.symbol)) fail('watchlist symbol');
    symbols.add(company.symbol);
  }
  const ids = new Set<string>();
  for (const entry of array(root.reports, 'reports')) {
    const report = object(entry, ['id', 'kind', 'title', 'symbols', 'as_of', 'generated_at', 'published_at', 'status', 'review_status', 'demo', 'summary', 'sections', 'metrics', 'sources', 'limitations'], 'report');
    string(report.id, 'report id');
    if (!/^(fundamental|news|social)-[a-z0-9-]+$/.test(report.id) || ids.has(report.id)) fail('report id');
    ids.add(report.id);
    if (!kinds.includes(report.kind as Kind) || report.status !== 'complete' || report.review_status !== 'unreviewed' || typeof report.demo !== 'boolean') fail('report state');
    string(report.title, 'title'); if (typeof report.summary !== 'string') fail('summary');
    for (const field of ['as_of', 'generated_at', 'published_at']) timestamp(report[field], field);
    if (Date.parse(report.as_of as string) > Date.parse(report.generated_at as string) || Date.parse(report.generated_at as string) > Date.parse(report.published_at as string)) fail('report timestamp order');
    const reportSymbols = array(report.symbols, 'symbols');
    if (new Set(reportSymbols).size !== reportSymbols.length || reportSymbols.some(symbol => !symbols.has(symbol as string))) fail('report symbols');
    const sources = new Set<string>();
    for (const entry of array(report.sources, 'sources')) {
      const source = object(entry, ['id', 'title', 'url', 'publisher', 'published_at', 'published_date', 'publication_precision'], 'source');
      for (const field of ['id', 'title', 'url', 'publisher']) string(source[field], `source ${field}`);
      if (sources.has(source.id as string)) fail('source id');
      let url: URL; try { url = new URL(source.url as string); } catch { fail('source URL'); }
      if (url.protocol !== 'https:' || url.username || url.password) fail('source URL');
      if (source.published_at !== null) timestamp(source.published_at, 'source published_at');
      if (source.published_date !== null) {
        string(source.published_date, 'source published_date');
        if (!/^\d{4}-\d{2}-\d{2}$/.test(source.published_date) || !Number.isFinite(Date.parse(source.published_date)) || new Date(source.published_date).toISOString().slice(0, 10) !== source.published_date) fail('source published_date');
      }
      if (source.publication_precision === 'date') {
        if (source.published_date === null || source.published_at !== null) fail('source date precision');
      } else if (source.publication_precision === 'timestamp') {
        if (source.published_at === null) fail('source timestamp precision');
      } else if (source.publication_precision !== null || source.published_at !== null || source.published_date !== null) fail('source publication precision');
      sources.add(source.id as string);
    }
    const refs = (value: unknown) => { const ids = array(value, 'source_ids'); if (new Set(ids).size !== ids.length) fail('duplicate source reference'); for (const id of ids) if (!sources.has(id as string)) fail('unknown source reference'); };
    for (const entry of array(report.sections, 'sections')) {
      const section = object(entry, ['heading', 'paragraphs'], 'section'); string(section.heading, 'section heading');
      for (const entry of array(section.paragraphs, 'paragraphs')) {
        const paragraph = object(entry, ['text', 'kind', 'source_ids'], 'paragraph');
        string(paragraph.text, 'paragraph text');
        if (!paragraphKinds.includes(paragraph.kind as ParagraphKind)) fail('paragraph kind');
        refs(paragraph.source_ids);
      }
    }
    for (const entry of array(report.metrics, 'metrics')) {
      const metric = object(entry, ['label', 'value', 'unit', 'period', 'source_ids'], 'metric');
      for (const field of ['label', 'value', 'unit', 'period']) string(metric[field], `metric ${field}`);
      refs(metric.source_ids);
    }
    for (const limitation of array(report.limitations, 'limitations')) string(limitation, 'limitation');
  }
  return value as Publication;
}
const file = process.env.ZILNON_PUBLICATION_FILE || resolve(process.cwd(), '../examples/reports.json');
export const publication = validatePublication(JSON.parse(readFileSync(file, 'utf8')));
export const reports = [...publication.reports].sort((a, b) => Date.parse(b.generated_at) - Date.parse(a.generated_at) || Date.parse(b.published_at) - Date.parse(a.published_at) || a.id.localeCompare(b.id));
export const labels: Record<Kind, string> = { fundamental: 'Fundamentals', news: 'News analysis', social: 'Social research' };
export const paragraphLabels: Record<ParagraphKind, string> = { source_fact: 'Source fact', source_claim: 'Source claim', interpretation: 'Interpretation', uncertainty: 'Uncertainty', research_question: 'Research question', summary: 'Summary' };
export function date(value: string, full = false) {
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Singapore', day: 'numeric', month: 'short', year: 'numeric', ...(full ? { hour: '2-digit', minute: '2-digit', hour12: false } : {}) }).format(new Date(value)) + (full ? ' SGT' : '');
}

export function calendarDate(value: string) {
  const [year, month, day] = value.split('-');
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${Number(day)} ${months[Number(month) - 1]} ${year}`;
}

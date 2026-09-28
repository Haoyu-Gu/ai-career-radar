import { useEffect, useMemo, useState } from 'react'
import {
  ArrowDownUp,
  BarChart3,
  BookOpen,
  BriefcaseBusiness,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Database,
  Download,
  ExternalLink,
  FileSearch,
  RefreshCw,
  Search,
  UsersRound,
} from 'lucide-react'
import Chart from './components/Chart'
import { api, fmt, percent } from './lib/api'
import type { EChartsOption } from 'echarts'

type Tab = 'overview' | 'talent' | 'direction' | 'jobs' | 'coverage'

type Benchmark = {
  available: boolean
  target_jobs: number
  target_companies: number
  target_programs: number
  official_fetched_jobs: number
  official_quantity_ratio: number | null
  matched_jobs: number
  job_recall: number | null
  company_coverage: number | null
  note: string
  companies: Array<{ company: string; target: number; official_reported: number; official_fetched: number; live_jobs: number; exact_matches: number; title_location_matches: number; matched_jobs: number; recall: number }>
  programs: Array<{ company: string; program: string; target: number }>
}

type Coverage = {
  candidate_companies: number
  checked_companies: number
  connected_companies: number
  complete_scans: number
  partial_scans: number
  failed_scans: number
  reported_total: number
  fetched_total: number
  updated_at: string | null
  open_jobs: number
  salary_disclosure_rate: number | null
  papers_stored: number
  identified_authors: number
  author_id_coverage: number | null
  research_retrieval: { matched: number; fetched: number; classified: number; relevant: number; pages: number; complete_directions: number; directions: number; average_coverage: number | null }
  automation: { enabled: boolean; refresh_hours: number; scheduler_check_minutes: number }
  exchange_rate: { rate: number; effective_date: string; source_url: string; is_fallback: boolean }
  benchmark: Benchmark
}

type DirectionRow = {
  slug: string
  name_zh: string
  dimension: string
  description: string
  open_jobs: number
  possible_jobs: number
  talent_program_jobs: number
  talent_program_possible: number
  employers: number
  top_employer_share: number | null
  salary_comparable_n: number
  salary_summary: null | { currency: string; n: number; p25: number; median: number; p75: number; note: string }
  known_headcount: number
  headcount_jobs: number
  early_career_jobs: number
  papers: number
  papers_recent_12m: number
  papers_prior_12m: number
  paper_growth: number | null
  active_authors: number
  institutions: number
  research_scope: null | { matched_total: number; fetched_count: number; relevant_count: number; is_complete: number; pages_fetched: number; coverage_ratio: number | null; query_strategy: string | null; year_start: number | null; year_end: number | null; error: string | null }
  value_score: number
  job_score: number
  research_heat_score: number
  research_heat_raw_score: number
  research_confidence: number
  personal_difficulty: {
    score: number
    sample_size: number
    research_barrier: number
    engineering_barrier: number
    systems_barrier: number
    profile: string
    note: string
  }
  value_score_method: string
}

type Overview = { coverage: Coverage; directions: DirectionRow[] }
type OverviewFilters = { market: string; city: string; education: string; experience: string; recruitment_type: string }
type FactorRow = { slug: string; name_zh: string; category: 'technology' | 'candidate'; jobs: number; share: number; companies: number; programs: number; title_hits: number; requirement_hits: number; top_terms: Array<{ term: string; jobs: number }>; examples: Array<{ id: number; company: string; program: string; title: string }> }
type FactorData = { jobs_analyzed: number; factors: FactorRow[]; cooccurrences: Array<{ left_name: string; right_name: string; jobs: number }>; method: string; limitation: string }

type Job = {
  id: number
  company: string
  market: string
  title: string
  department: string | null
  location: string | null
  recruitment_type: string
  work_nature: string
  education: string
  experience: string
  salary_raw: string | null
  salary_cny_lower?: number | null
  salary_cny_upper?: number | null
  annual_cny_lower?: number | null
  annual_cny_upper?: number | null
  original_currency?: string | null
  salary_period?: string | null
  url: string
  last_seen_at: string
}

type Paper = {
  id: number
  title: string
  earliest_public_date: string
  source_url: string
  authors: string | null
}

type DirectionDetail = {
  direction: DirectionRow
  paper_monthly: Array<{ date: string; value: number }>
  authors_rolling_12m: Array<{ date: string; value: number }>
  job_snapshots: Array<{ date: string; value: number; completeness: string }>
  forecast: { enabled: boolean; reason: string; method: string | null; values: number[] | null; lower: number[] | null; upper: number[] | null; dates: string[]; confidence: string | null; basis: string | null }
  scope: null | { matched_total: number; fetched_count: number; classified_count: number; relevant_count: number; is_complete: number; pages_fetched: number; coverage_ratio: number | null; query_strategy: string | null; year_start: number | null; year_end: number | null; error: string | null }
  jobs: Job[]
  papers: Paper[]
  limitations: string[]
}

const tabs: Array<{ key: Tab; label: string; icon: typeof BarChart3 }> = [
  { key: 'overview', label: '方向选择', icon: BarChart3 },
  { key: 'talent', label: '人才计划与因子', icon: UsersRound },
  { key: 'direction', label: '方向详情', icon: FileSearch },
  { key: 'jobs', label: '岗位列表', icon: BriefcaseBusiness },
  { key: 'coverage', label: '抓取进度', icon: Database },
]

const marketLabels: Record<string, string> = {
  '': '全部市场',
  cn_internet: '中国互联网',
  cn_ai: '中国 AI 公司',
  overseas_internet: '海外互联网',
  overseas_ai: '海外 AI 公司',
}

function App() {
  const [tab, setTab] = useState<Tab>('overview')
  const [overview, setOverview] = useState<Overview | null>(null)
  const [overviewFilters, setOverviewFilters] = useState<OverviewFilters>({ market: '', city: '', education: '', experience: '', recruitment_type: '' })
  const [activeDirection, setActiveDirection] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadOverview = async (nextFilters = overviewFilters) => {
    setLoading(true)
    setError('')
    try {
      const query = new URLSearchParams(Object.entries(nextFilters).filter(([, value]) => value))
      const result = await api<Overview>(`/api/overview${query.size ? `?${query}` : ''}`)
      setOverview(result)
      if (!activeDirection && result.directions.length) {
        setActiveDirection([...result.directions].sort((a, b) => b.talent_program_jobs - a.talent_program_jobs)[0].slug)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '无法读取数据')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void loadOverview() }, [])

  const navigateDirection = (slug: string) => {
    setActiveDirection(slug)
    setTab('direction')
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><span /></div>
          <div><strong>AI 就业雷达</strong><small>岗位 × 人才计划 × 研究辅助</small></div>
        </div>
        <nav aria-label="主导航">
          {tabs.map(({ key, label, icon: Icon }) => (
            <button key={key} className={tab === key ? 'nav-item active' : 'nav-item'} onClick={() => setTab(key)}>
              <Icon size={18} /> <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="status-dot" /> 本地数据库
          <small>{overview?.coverage.updated_at ? `更新 ${formatTime(overview.coverage.updated_at)}` : '等待首次刷新'}</small>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <p className="eyebrow">PERSONAL RESEARCH DESK</p>
            <h1>{tabs.find((item) => item.key === tab)?.label}</h1>
          </div>
          <div className="top-actions">
            <span className="method-badge">规则分类 {overview?.coverage ? '· 可追溯' : ''}</span>
            <button className="icon-button" title="重新载入页面数据" onClick={() => void loadOverview()}><RefreshCw size={18} /></button>
          </div>
        </header>

        {error && <div className="error-banner"><CircleAlert size={18} />{error}</div>}
        {loading && !overview ? <LoadingState /> : null}
        {overview && tab === 'overview' && (
          <OverviewPage
            data={overview}
            filters={overviewFilters}
            onFilters={(next) => { setOverviewFilters(next); void loadOverview(next) }}
            onDirection={navigateDirection}
          />
        )}
        {overview && tab === 'direction' && (
          <DirectionPage directions={overview.directions} slug={activeDirection} onSlug={setActiveDirection} />
        )}
        {tab === 'talent' && <TalentPage />}
        {tab === 'jobs' && <JobsPage directions={overview?.directions ?? []} initialDirection="" />}
        {tab === 'coverage' && <CoveragePage onComplete={() => void loadOverview()} />}
      </main>
    </div>
  )
}

function LoadingState() {
  return <div className="loading-state"><RefreshCw className="spin" size={22} /><span>正在读取本地指标…</span></div>
}

function OverviewPage({ data, filters, onFilters, onDirection }: {
  data: Overview; filters: OverviewFilters; onFilters: (value: OverviewFilters) => void; onDirection: (slug: string) => void
}) {
  const [sort, setSort] = useState<'value_score' | 'job_score' | 'research_heat_score' | 'personal_difficulty'>('value_score')
  const [selected, setSelected] = useState<string[]>(() => [...data.directions].sort((a, b) => b.talent_program_jobs - a.talent_program_jobs).slice(0, 3).map((item) => item.slug))
  const sorted = useMemo(() => [...data.directions].sort((a, b) => {
    if (sort === 'personal_difficulty') return a.personal_difficulty.score - b.personal_difficulty.score
    return Number(b[sort]) - Number(a[sort])
  }), [data.directions, sort])
  const compared = data.directions.filter((item) => selected.includes(item.slug))

  const toggle = (slug: string) => {
    setSelected((current) => current.includes(slug)
      ? current.length > 2 ? current.filter((item) => item !== slug) : current
      : current.length < 4 ? [...current, slug] : current)
  }

  return (
    <div className="page-stack">
      <section className="filter-bar">
        <label>市场<select value={filters.market} onChange={(e) => onFilters({ ...filters, market: e.target.value })}>{Object.entries(marketLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <label>城市<select value={filters.city} onChange={(e) => onFilters({ ...filters, city: e.target.value })}><option value="">全部城市</option><option>北京市</option><option>上海市</option><option>深圳市</option><option>杭州</option><option>San Francisco</option><option>New York</option><option>London</option></select></label>
        <label>经验<select value={filters.experience} onChange={(e) => onFilters({ ...filters, experience: e.target.value })}><option value="">全部经验</option><option value="0-1y">0–1年</option><option value="2-4y">2–4年</option><option value="5-9y">5–9年</option><option value="10y+">10年以上</option><option value="unknown">未知</option></select></label>
        <label>学历<select value={filters.education} onChange={(e) => onFilters({ ...filters, education: e.target.value })}><option value="">全部学历</option><option value="bachelor">本科</option><option value="master">硕士</option><option value="phd">博士</option><option value="unknown">未知</option></select></label>
        <label>招聘类型<select value={filters.recruitment_type} onChange={(e) => onFilters({ ...filters, recruitment_type: e.target.value })}><option value="">全部类型</option><option value="campus">校招</option><option value="experienced">社招</option><option value="internship">实习</option><option value="unknown">未知</option></select></label>
        <label>时间<select disabled><option>最近 5 年研究 / 当前岗位</option></select></label>
        <label>方向<select defaultValue="" onChange={(e) => e.target.value && onDirection(e.target.value)}><option value="">全部方向</option>{data.directions.map((d) => <option value={d.slug} key={d.slug}>{d.name_zh}</option>)}</select></label>
        <span className="filter-scope">先看专项人才计划和真实岗位数量；薪资、论文热度只作辅助。</span>
      </section>

      <section className="metric-grid">
        <MetricCard label="人才计划岗位基准" value={fmt(data.coverage.benchmark.target_jobs)} note={`${data.coverage.benchmark.target_companies} 家公司 · ${data.coverage.benchmark.target_programs} 个计划`} tone="blue" />
        <MetricCard label="官网抓取召回" value={percent(data.coverage.benchmark.job_recall)} note={`${data.coverage.benchmark.matched_jobs} / ${data.coverage.benchmark.target_jobs} 条与基准匹配`} tone="teal" />
        <MetricCard label="公开薪资比例" value={percent(data.coverage.salary_disclosure_rate)} note={`统一换算人民币 · 汇率 ${data.coverage.exchange_rate.effective_date}`} tone="amber" />
        <MetricCard label="论文采集质量" value={`${data.coverage.research_retrieval.complete_directions}/${data.coverage.research_retrieval.directions || 14} 完整`} note={`${fmt(data.coverage.research_retrieval.fetched)} 条 · ${fmt(data.coverage.research_retrieval.pages)} 页；不完整自动降权`} tone="violet" />
      </section>

      <section className="panel compare-panel">
        <div className="panel-heading">
          <div><p className="section-kicker">方向对比</p><h2>人才计划把岗位投向了哪里</h2></div>
          <span className="muted">原始数量保留，性价比另行透明计算</span>
        </div>
        <div className="direction-chips">
          {data.directions.map((direction) => (
            <button key={direction.slug} onClick={() => toggle(direction.slug)} className={selected.includes(direction.slug) ? 'chip selected' : 'chip'}>
              <span>{direction.name_zh}</span><small>{direction.dimension === 'application' ? '应用' : '技术'}</small>
            </button>
          ))}
        </div>
        <div className="compare-grid">
          <MiniBars title="专项人才计划岗位" rows={compared} field="talent_program_jobs" color="#0f766e" />
          <MiniBars title="当前官网在招" rows={compared} field="open_jobs" color="#2563eb" />
          <MiniBars title="校招与实习" rows={compared} field="early_career_jobs" color="#7c3aed" />
        </div>
      </section>

      <section className="panel table-panel">
        <div className="panel-heading">
          <div><p className="section-kicker">方向性价比 DVI</p><h2>岗位、研究热度和对你难度</h2><p className="score-formula">50% 岗位数量 + 20% 研究热度 + 30% 低个人困难度</p></div>
          <label className="sort-control"><ArrowDownUp size={15} />排序<select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}><option value="value_score">方向性价比</option><option value="job_score">岗位数量分</option><option value="research_heat_score">研究热度分</option><option value="personal_difficulty">个人困难度（低优先）</option></select></label>
        </div>
        <div className="profile-note"><strong>计算口径：</strong>岗位分以人才计划为主；研究热度综合论文量、活跃作者和近 12 个月增长，并按实际抓取覆盖率降权。个人困难度按你的年龄阶段和可迁移科研能力校准，不因简历已有方向加分。</div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>方向</th><th>性价比</th><th>岗位数量</th><th>研究热度</th><th>对你难度</th><th /></tr></thead>
            <tbody>
              {sorted.map((row) => (
                <tr key={row.slug}>
                  <td><strong>{row.name_zh}</strong><small>{row.description}</small></td>
                  <td><span className={`score-pill ${row.value_score >= 80 ? 'high' : row.value_score >= 65 ? 'medium' : 'low'}`}>{row.value_score.toFixed(1)}</span><small>{row.value_score >= 80 ? '优先投入' : row.value_score >= 65 ? '值得布局' : row.value_score >= 50 ? '先补短板' : '投入产出偏低'}</small></td>
                  <td><strong>{row.job_score.toFixed(1)} 分</strong><small>人才计划 {fmt(row.talent_program_jobs)} · 当前 {fmt(row.open_jobs)}</small></td>
                  <td><strong>{row.research_heat_score.toFixed(1)} 分</strong><small>原始 {row.research_heat_raw_score.toFixed(1)} · 置信度 {percent(row.research_confidence)}</small><small>{fmt(row.papers)} 篇 · 近 12 月增长 {percent(row.paper_growth)}</small></td>
                  <td><strong className="difficulty-score">{row.personal_difficulty.score.toFixed(1)} 分</strong><small>科研 {row.personal_difficulty.research_barrier.toFixed(1)} · 工程 {row.personal_difficulty.engineering_barrier.toFixed(1)} · 系统 {row.personal_difficulty.systems_barrier.toFixed(1)}</small></td>
                  <td><button className="row-action" onClick={() => onDirection(row.slug)} aria-label={`查看${row.name_zh}`}><ChevronRight size={18} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

function MetricCard({ label, value, note, tone }: { label: string; value: string; note: string; tone: string }) {
  return <article className={`metric-card ${tone}`}><span>{label}</span><strong>{value}</strong><small>{note}</small></article>
}

function MiniBars({ title, rows, field, color }: { title: string; rows: DirectionRow[]; field: 'open_jobs' | 'papers' | 'active_authors' | 'early_career_jobs' | 'known_headcount' | 'talent_program_jobs'; color: string }) {
  const max = Math.max(1, ...rows.map((row) => row[field]))
  return <div className="mini-bars"><h3>{title}</h3>{rows.map((row) => <div className="bar-row" key={row.slug}><span>{row.name_zh}</span><div><i style={{ width: `${Math.max(3, row[field] / max * 100)}%`, background: color }} /></div><strong>{fmt(row[field])}</strong></div>)}</div>
}

function DirectionPage({ directions, slug, onSlug }: { directions: DirectionRow[]; slug: string; onSlug: (slug: string) => void }) {
  const [detail, setDetail] = useState<DirectionDetail | null>(null)
  const [view, setView] = useState<'raw' | 'index'>('raw')
  const [explanation, setExplanation] = useState<{ mode: string; summary: string; limitations: string[]; sources: string[] } | null>(null)
  const [busy, setBusy] = useState(true)

  useEffect(() => {
    if (!slug) return
    setBusy(true)
    setExplanation(null)
    api<DirectionDetail>(`/api/directions/${slug}`).then(setDetail).finally(() => setBusy(false))
  }, [slug])

  const indexSeries = (data: Array<{ date: string; value: number }>) => {
    const base = data.find((item) => item.value > 0)?.value
    return data.map((item) => ({ ...item, value: base ? item.value / base * 100 : 0 }))
  }

  if (busy || !detail) return <LoadingState />
  const paper = view === 'index' ? indexSeries(detail.paper_monthly) : detail.paper_monthly
  const authors = view === 'index' ? indexSeries(detail.authors_rolling_12m) : detail.authors_rolling_12m
  const lineOption: EChartsOption = {
    color: ['#2563eb', '#7c3aed'],
    tooltip: { trigger: 'axis' },
    legend: { data: ['论文', '滚动12月作者'], bottom: 0, textStyle: { color: '#536174' } },
    grid: { left: 50, right: 28, top: 32, bottom: 52 },
    xAxis: { type: 'category', data: paper.map((item) => item.date), axisLabel: { color: '#718096' }, axisLine: { lineStyle: { color: '#dbe2ea' } } },
    yAxis: { type: 'value', name: view === 'index' ? '基期=100' : '数量', splitLine: { lineStyle: { color: '#edf1f5' } }, axisLabel: { color: '#718096' } },
    series: [
      { name: '论文', type: 'line', smooth: true, symbol: 'none', data: paper.map((item) => item.value), lineStyle: { width: 2.5 }, areaStyle: { opacity: .06 } },
      { name: '滚动12月作者', type: 'line', smooth: true, symbol: 'none', data: authors.map((item) => item.value), lineStyle: { width: 2 } },
    ],
  }
  const forecastOption: EChartsOption = {
    tooltip: { trigger: 'axis' },
    grid: { left: 44, right: 18, top: 24, bottom: 36 },
    xAxis: { type: 'category', data: detail.forecast.dates, axisLabel: { color: '#718096' } },
    yAxis: { type: 'value', min: 0, splitLine: { lineStyle: { color: '#edf1f5' } } },
    series: [
      { name: '预测', type: 'line', data: detail.forecast.values || [], symbolSize: 8, lineStyle: { width: 3, color: '#7c3aed' }, itemStyle: { color: '#7c3aed' } },
      { name: '参考上沿', type: 'line', data: detail.forecast.upper || [], symbol: 'none', lineStyle: { type: 'dashed', color: '#c4b5fd' } },
      { name: '参考下沿', type: 'line', data: detail.forecast.lower || [], symbol: 'none', lineStyle: { type: 'dashed', color: '#c4b5fd' } },
    ],
  }

  return <div className="page-stack">
    <section className="detail-title-row">
      <div><p className="section-kicker">DIRECTION DOSSIER</p><h2>{detail.direction.name_zh}</h2><p>{detail.direction.description}</p></div>
      <label>切换方向<select value={slug} onChange={(e) => onSlug(e.target.value)}>{directions.map((item) => <option value={item.slug} key={item.slug}>{item.name_zh}</option>)}</select></label>
    </section>
    <section className="scope-strip">
      <span><BookOpen size={17} />搜索到 <strong>{fmt(detail.scope?.matched_total)}</strong></span>
      <span>实际读取 <strong>{fmt(detail.scope?.fetched_count)}</strong></span>
      <span>分页 <strong>{fmt(detail.scope?.pages_fetched)}</strong></span>
      <span>逐篇判断 <strong>{fmt(detail.scope?.classified_count)}</strong></span>
      <span>确认相关 <strong>{fmt(detail.scope?.relevant_count)}</strong></span>
      <span>覆盖率 <strong>{percent(detail.scope?.coverage_ratio)}</strong></span>
      <b className={detail.scope?.is_complete ? 'complete' : 'sample'}>{detail.scope?.is_complete ? '已经读完' : '部分数据 · 已降权'}</b>
    </section>
    <section className="panel chart-panel">
      <div className="panel-heading"><div><p className="section-kicker">研究热度</p><h2>论文和研究者有没有变多</h2></div><div className="segmented"><button className={view === 'raw' ? 'active' : ''} onClick={() => setView('raw')}>实际数量</button><button className={view === 'index' ? 'active' : ''} onClick={() => setView('index')}>看涨跌幅</button></div></div>
      <Chart option={lineOption} height={360} />
      <p className="chart-note">紫线表示过去一年里做过这个方向的不同作者人数；本月还没结束，数字会继续变化。</p>
    </section>
    <section className="split-grid">
      <div className="panel compact-panel">
        <div className="panel-heading"><div><p className="section-kicker">岗位变化</p><h2>这个方向的岗位是在变多吗</h2></div></div>
        {detail.job_snapshots.length < 2 ? <EmptyState title="还要再更新几次" text="目前只有第一次记录。以后每次刷新都会留下岗位数量，积累后就能看真实涨跌。" /> : <Chart height={240} option={{ xAxis: { type: 'category', data: detail.job_snapshots.map((x) => x.date) }, yAxis: { type: 'value' }, series: [{ type: 'line', data: detail.job_snapshots.map((x) => x.value) }] }} />}
      </div>
      <div className="panel compact-panel">
        <div className="panel-heading"><div><p className="section-kicker">未来 3 个月</p><h2>研究热度辅助预测</h2></div><span className="status-badge partial">{detail.forecast.confidence === 'low' ? '低置信度' : '中等置信度'}</span></div>
        {detail.forecast.enabled && detail.forecast.values ? <><Chart height={200} option={forecastOption} /><p className="chart-note">{detail.forecast.reason}</p></> : <EmptyState title="暂时算不了" text={detail.forecast.reason} />}
        <div className="gate-list"><span>不是岗位数量预测</span><span>用来辅助看方向</span><span>有招聘历史后再升级</span></div>
      </div>
    </section>
    <section className="split-grid">
      <EvidenceList title="代表岗位" icon={BriefcaseBusiness} items={detail.jobs.map((job) => ({ title: job.title, meta: `${job.company} · ${job.location || '地点未知'}`, href: job.url, tag: formatSalaryCny(job) }))} />
      <EvidenceList title="代表论文" icon={BookOpen} items={detail.papers.map((paper) => ({ title: paper.title, meta: `${paper.earliest_public_date || '日期未知'} · ${paper.authors?.split(',').slice(0, 3).join('、') || '作者待识别'}`, href: paper.source_url, tag: '来源记录' }))} />
    </section>
    <section className="panel explanation-panel">
      <div className="panel-heading"><div><p className="section-kicker">方向建议</p><h2>这些数字该怎么看</h2></div><button className="primary-button" onClick={() => api(`/api/directions/${slug}/explain`, { method: 'POST' }).then((value) => setExplanation(value as typeof explanation))}>{explanation ? '重新分析' : '帮我分析'}</button></div>
      {explanation ? <><p className="explanation-text">{explanation.summary}</p><div className="source-links">{explanation.sources.map((url) => <a href={url} target="_blank" rel="noreferrer" key={url}>查看原始信息 <ExternalLink size={13} /></a>)}</div></> : <p className="muted">点击后会先看岗位数量、招聘公司和校招机会，再把薪资与研究热度作为辅助信息。</p>}
      <ul className="limitations">{detail.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
    </section>
  </div>
}

function EvidenceList({ title, icon: Icon, items }: { title: string; icon: typeof BookOpen; items: Array<{ title: string; meta: string; href: string; tag: string }> }) {
  return <section className="panel evidence-list"><div className="panel-heading"><div><p className="section-kicker"><Icon size={15} /> 原始信息</p><h2>{title}</h2></div></div>{items.length ? items.map((item) => <a className="evidence-item" href={item.href} target="_blank" rel="noreferrer" key={`${item.href}-${item.title}`}><div><strong>{item.title}</strong><small>{item.meta}</small></div><span>{item.tag}</span><ExternalLink size={15} /></a>) : <EmptyState title="目前没有记录" text="可能是还没抓到，也可能是确实没有符合条件的内容。" />}</section>
}

function JobsPage({ directions, initialDirection }: { directions: DirectionRow[]; initialDirection: string }) {
  const [filters, setFilters] = useState({ search: '', market: '', direction: initialDirection, city: '', education: '', experience: '', recruitment_type: '', sort: 'updated' })
  const [data, setData] = useState<{ total: number; items: Job[]; exchange_rate?: Coverage['exchange_rate'] }>({ total: 0, items: [] })
  const [expanded, setExpanded] = useState<number | null>(null)
  const [details, setDetails] = useState<Record<number, Job & { responsibilities: string; requirements: string; directions: Array<{ name_zh: string }> }>>({})

  const load = async () => {
    const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value))
    setData(await api(`/api/jobs?${query}`))
  }
  useEffect(() => { void load() }, [filters.market, filters.direction, filters.education, filters.experience, filters.recruitment_type, filters.sort])
  const open = async (id: number) => {
    setExpanded(expanded === id ? null : id)
    if (!details[id]) {
      const detail = await api<Job & { responsibilities: string; requirements: string; directions: Array<{ name_zh: string }> }>(`/api/jobs/${id}`)
      setDetails((current) => ({ ...current, [id]: detail }))
    }
  }
  const exportQuery = new URLSearchParams(Object.entries(filters).filter(([key, value]) => value && key !== 'sort'))

  return <div className="page-stack">
    <section className="job-search-panel">
      <div className="search-box"><Search size={19} /><input value={filters.search} onChange={(e) => setFilters({ ...filters, search: e.target.value })} onKeyDown={(e) => e.key === 'Enter' && void load()} placeholder="搜索岗位、部门或要求" /><button onClick={() => void load()}>搜索</button></div>
      <div className="job-filters">
        <select value={filters.market} onChange={(e) => setFilters({ ...filters, market: e.target.value })}>{Object.entries(marketLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select>
        <select value={filters.direction} onChange={(e) => setFilters({ ...filters, direction: e.target.value })}><option value="">全部方向</option>{directions.map((d) => <option value={d.slug} key={d.slug}>{d.name_zh}</option>)}</select>
        <input className="filter-input" value={filters.city} onChange={(e) => setFilters({ ...filters, city: e.target.value })} onBlur={() => void load()} placeholder="城市（如 北京市）" />
        <select value={filters.education} onChange={(e) => setFilters({ ...filters, education: e.target.value })}><option value="">全部学历</option><option value="bachelor">本科</option><option value="master">硕士</option><option value="phd">博士</option><option value="unknown">未知</option></select>
        <select value={filters.experience} onChange={(e) => setFilters({ ...filters, experience: e.target.value })}><option value="">全部经验</option><option value="0-1y">0–1年</option><option value="2-4y">2–4年</option><option value="5-9y">5–9年</option><option value="10y+">10年以上</option><option value="unknown">未知</option></select>
        <select value={filters.recruitment_type} onChange={(e) => setFilters({ ...filters, recruitment_type: e.target.value })}><option value="">全部招聘类型</option><option value="campus">校招</option><option value="experienced">社招</option><option value="internship">实习</option><option value="unknown">未知</option></select>
        <select value={filters.sort} onChange={(e) => setFilters({ ...filters, sort: e.target.value })}><option value="updated">最近核验</option><option value="company">公司</option><option value="salary_high">薪资上限</option><option value="experience">经验层级</option></select>
        <a className="export-button" href={`/api/jobs.csv?${exportQuery}`}><Download size={16} />导出当前结果</a>
      </div>
    </section>
    <section className="panel jobs-panel">
      <div className="panel-heading"><div><p className="section-kicker">正在招聘</p><h2>{fmt(data.total)} 个岗位</h2></div><span className="muted">薪资统一显示人民币；没公开的不猜</span></div>
      {data.items.length ? <div className="job-list">{data.items.map((job) => <div className={expanded === job.id ? 'job-row expanded' : 'job-row'} key={job.id}>
        <button className="job-row-main" onClick={() => void open(job.id)}>
          <div className="company-avatar">{job.company.slice(0, 1)}</div>
          <div className="job-title"><strong>{job.title}</strong><span>{job.company} · {job.location || '地点未知'}</span></div>
          <span className="job-type">{label(job.work_nature)}</span>
          <div className="salary-cell"><strong>{formatSalaryCny(job)}</strong><small>{job.annual_cny_lower || job.salary_cny_lower ? `${job.original_currency === 'USD' ? '按最新汇率折算 · ' : ''}${job.salary_raw || '招聘页面标价'}` : '招聘页面没有公开数字'}</small></div>
          <ChevronDown size={18} />
        </button>
        {expanded === job.id && <div className="job-detail">
          {!details[job.id] ? <span className="muted">正在读取原始记录…</span> : <>
            <div className="detail-tags"><span>{label(details[job.id].education)}</span><span>{label(details[job.id].experience)}</span><span>{label(details[job.id].recruitment_type)}</span>{details[job.id].directions.map((d) => <span key={d.name_zh}>{d.name_zh}</span>)}</div>
            <p>{(details[job.id].responsibilities || details[job.id].requirements || '').slice(0, 900)}</p>
            <a href={job.url} target="_blank" rel="noreferrer">查看原始 JD <ExternalLink size={14} /></a>
          </>}
        </div>}
      </div>)}</div> : <EmptyState title="没有找到符合条件的岗位" text="可以换个地区或方向，也可能是这家公司的页面还没有成功抓取。" />}
    </section>
  </div>
}

function TalentPage() {
  const [benchmark, setBenchmark] = useState<Benchmark | null>(null)
  const [factorData, setFactorData] = useState<FactorData | null>(null)
  useEffect(() => {
    void Promise.all([api<Benchmark>('/api/benchmark'), api<FactorData>('/api/jd-factors')]).then(([b, f]) => {
      setBenchmark(b); setFactorData(f)
    })
  }, [])
  if (!benchmark || !factorData) return <LoadingState />
  const technologies = factorData.factors.filter((item) => item.category === 'technology')
  const candidate = factorData.factors.filter((item) => item.category === 'candidate')
  return <div className="page-stack">
    <section className="coverage-hero talent-hero">
      <div><p className="section-kicker">人才计划验收基准</p><h2>{fmt(benchmark.target_jobs)} 条 JD，逐条追官网</h2><p>{benchmark.target_companies} 家公司、{benchmark.target_programs} 个专项计划；参考快照和实时抓取分开存，不混成“当前在招”。</p></div>
      <div className="recall-number"><strong>{percent(benchmark.job_recall)}</strong><span>当前召回率</span></div>
    </section>
    <section className="metric-grid">
      <MetricCard label="基准岗位" value={fmt(benchmark.target_jobs)} note="同学提供的外部验收快照" tone="blue" />
      <MetricCard label="官网原始抓取" value={fmt(benchmark.official_fetched_jobs)} note={`达到基准数量的 ${percent(benchmark.official_quantity_ratio)}`} tone="violet" />
      <MetricCard label="已匹配官网岗位" value={fmt(benchmark.matched_jobs)} note="官方 ID 或公司+标题+地点命中" tone="teal" />
      <MetricCard label="公司覆盖" value={percent(benchmark.company_coverage)} note="至少匹配到一条才算覆盖" tone="amber" />
      <MetricCard label="分析过的 JD" value={fmt(factorData.jobs_analyzed)} note="职责和要求分开统计" tone="violet" />
    </section>
    <section className="panel table-panel">
      <div className="panel-heading"><div><p className="section-kicker">逐公司验收</p><h2>差多少，一眼能看见</h2></div><span className="muted">不拿普通岗位冒充人才计划召回</span></div>
      <div className="table-wrap"><table><thead><tr><th>公司</th><th>人才计划基准</th><th>官网原始抓取</th><th>AI 相关岗位</th><th>匹配岗位</th><th>召回率</th></tr></thead><tbody>{benchmark.companies.map((item) => <tr key={item.company}><td><strong>{item.company}</strong></td><td>{fmt(item.target)}</td><td>{fmt(item.official_fetched)}</td><td>{fmt(item.live_jobs)}</td><td>{fmt(item.matched_jobs)}<small>ID 精确匹配 {item.exact_matches}</small></td><td><strong>{percent(item.recall)}</strong></td></tr>)}</tbody></table></div>
    </section>
    <FactorPanel title="企业在押注什么技术" rows={technologies} />
    <FactorPanel title="候选人被反复要求什么" rows={candidate} />
    <section className="panel table-panel">
      <div className="panel-heading"><div><p className="section-kicker">因子共现</p><h2>哪些要求经常一起出现</h2></div><span className="muted">同一 JD 同时命中</span></div>
      <div className="factor-pairs">{factorData.cooccurrences.map((item) => <div key={`${item.left_name}-${item.right_name}`}><span>{item.left_name}</span><b>×</b><span>{item.right_name}</span><strong>{fmt(item.jobs)} 条</strong></div>)}</div>
      <p className="factor-note">{factorData.method} {factorData.limitation}</p>
    </section>
  </div>
}

function FactorPanel({ title, rows }: { title: string; rows: FactorRow[] }) {
  const max = Math.max(1, ...rows.map((item) => item.jobs))
  return <section className="panel factor-panel">
    <div className="panel-heading"><div><p className="section-kicker">JD 因子</p><h2>{title}</h2></div><span className="muted">按命中岗位数排序</span></div>
    <div className="factor-grid">{rows.map((item) => <article className="factor-card" key={item.slug}>
      <div><strong>{item.name_zh}</strong><span>{percent(item.share)}</span></div>
      <div className="factor-track"><i style={{ width: `${item.jobs / max * 100}%` }} /></div>
      <p><b>{fmt(item.jobs)}</b> 条岗位 · {item.companies} 家公司 · {item.programs} 个计划</p>
      <small>要求栏命中 {item.requirement_hits} · 标题命中 {item.title_hits}</small>
      <div className="detail-tags">{item.top_terms.slice(0, 3).map((term) => <span key={term.term}>{term.term} {term.jobs}</span>)}</div>
    </article>)}</div>
  </section>
}

type Source = { id: number; company: string; group_name: string; market: string; adapter: string; status: string; careers_url: string | null; last_success_at: string | null; last_error: string | null; last_scan_status: string | null; reported_total: number | null; fetched_count: number | null; relevant_count: number | null; last_scan_at: string | null; scan_error: string | null }
type PaperSource = { source_slug: string; status: string; reported_total: number | null; fetched_count: number; relevant_count: number; finished_at: string | null; error: string | null; raw_path: string | null }

function CoveragePage({ onComplete }: { onComplete: () => void }) {
  const [data, setData] = useState<{ coverage: Coverage; items: Source[]; paper_sources: PaperSource[] } | null>(null)
  const [run, setRun] = useState<{ run_id: string; status: string; phase?: string; progress?: number; message?: string } | null>(null)
  const [statusFilter, setStatusFilter] = useState('')
  const load = () => api<{ coverage: Coverage; items: Source[]; paper_sources: PaperSource[] }>('/api/sources').then(setData)
  useEffect(() => { void load() }, [])
  useEffect(() => {
    if (!run?.run_id || ['complete', 'failed'].includes(run.status)) return
    const timer = window.setInterval(async () => {
      const next = await api<typeof run>(`/api/refresh/${run.run_id}`)
      setRun(next)
      if (next.status === 'complete') { void load(); onComplete() }
    }, 1200)
    return () => window.clearInterval(timer)
  }, [run?.run_id, run?.status])
  const start = async () => setRun(await api('/api/refresh', { method: 'POST' }))
  if (!data) return <LoadingState />
  const sources = data.items.filter((item) => !statusFilter || item.status === statusFilter)
  return <div className="page-stack">
    <section className="coverage-hero">
      <div><p className="section-kicker">抓取进度</p><h2>能抓多少就继续抓多少</h2><p>岗位与论文统一增量更新；异常下降会保护旧数据，论文分页不完整会自动降权。{data.coverage.automation.enabled ? ` 后台每 ${data.coverage.automation.refresh_hours} 小时自动检查。` : ''}</p></div>
      <button className="primary-button large" disabled={Boolean(run && !['complete', 'failed'].includes(run.status))} onClick={() => void start()}><RefreshCw className={run && !['complete', 'failed'].includes(run.status) ? 'spin' : ''} size={18} />{run && !['complete', 'failed'].includes(run.status) ? '更新中' : '刷新真实数据'}</button>
    </section>
    {run && <section className={`run-progress ${run.status}`}><div><strong>{run.phase || '准备'}</strong><span>{run.message}</span></div><div className="progress-track"><i style={{ width: `${(run.progress || 0) * 100}%` }} /></div><b>{Math.round((run.progress || 0) * 100)}%</b></section>}
    <section className="metric-grid coverage-metrics">
      <MetricCard label="已发现公司" value={fmt(data.coverage.candidate_companies)} note={`已经检查 ${data.coverage.checked_companies} 家`} tone="blue" />
      <MetricCard label="成功抓取" value={`${data.coverage.connected_companies} 家`} note={`完整 ${data.coverage.complete_scans} · 部分成功 ${data.coverage.partial_scans} · 失败 ${data.coverage.failed_scans}`} tone="teal" />
      <MetricCard label="岗位页面给出 / 实际拿到" value={`${fmt(data.coverage.reported_total)} / ${fmt(data.coverage.fetched_total)}`} note="差额会保留，不会偷偷当成 0" tone="violet" />
      <MetricCard label="美元换人民币" value={`1 美元 ≈ ${data.coverage.exchange_rate.rate.toFixed(3)} 元`} note={`${data.coverage.exchange_rate.effective_date} · ECB 参考汇率`} tone="amber" />
    </section>
    <section className="panel table-panel">
      <div className="panel-heading"><div><p className="section-kicker">论文来源</p><h2>分页、覆盖与错误都单独记录</h2></div><span className="muted">原始响应留档，可重新分类</span></div>
      <div className="table-wrap"><table><thead><tr><th>来源</th><th>状态</th><th>候选 / 抓取 / 相关</th><th>最后更新</th><th>异常</th></tr></thead><tbody>{data.paper_sources.map((source) => <tr key={source.source_slug}><td><strong>{{ openalex: 'OpenAlex 历史与统计', crossref: 'Crossref DOI 元数据', arxiv: 'arXiv RSS 最新增量' }[source.source_slug] || source.source_slug}</strong></td><td><StatusBadge status={source.status} /></td><td>{source.reported_total === null ? '未声明总量' : fmt(source.reported_total)} / {fmt(source.fetched_count)} / {fmt(source.relevant_count)}</td><td>{source.finished_at ? formatTime(source.finished_at) : '—'}</td><td className="reason-cell">{source.error || '—'}</td></tr>)}</tbody></table></div>
    </section>
    <section className="panel table-panel">
      <div className="panel-heading"><div><p className="section-kicker">逐家公司看</p><h2>哪些抓到了，哪些还没抓到</h2></div><select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}><option value="">全部状态</option><option value="connected">抓取成功</option><option value="accessible">找到招聘页</option><option value="partial">只抓到一部分</option><option value="failed">本次失败</option><option value="pending">还没检查</option></select></div>
      <div className="table-wrap"><table><thead><tr><th>公司</th><th>市场</th><th>招聘入口</th><th>当前状态</th><th>页面岗位 / 抓到 / AI 岗位</th><th>最后成功</th><th>没抓全的原因</th></tr></thead><tbody>{sources.map((source) => <tr key={source.id}><td><strong>{source.company}</strong><small>{source.group_name || '独立公司'}</small></td><td>{marketLabels[source.market] || source.market}</td><td>{source.careers_url ? <a href={source.careers_url} target="_blank" rel="noreferrer">打开官网<ExternalLink size={12} /></a> : '还在找'}</td><td><StatusBadge status={source.status} /></td><td>{source.reported_total === null ? '—' : `${source.reported_total} / ${source.fetched_count} / ${source.relevant_count}`}<small>{scanLabel(source.last_scan_status)}</small></td><td>{source.last_success_at ? formatTime(source.last_success_at) : '—'}</td><td className="reason-cell">{source.scan_error || source.last_error || '—'}</td></tr>)}</tbody></table></div>
    </section>
  </div>
}

function StatusBadge({ status }: { status: string }) {
  const labels: Record<string, string> = { connected: '抓取成功', complete: '完整', verified: '接口已核验', accessible: '已找到招聘页', partial: '只抓到一部分', failed: '本次失败', pending: '还没检查', blocked: '网站限制访问', running: '正在抓取' }
  return <span className={`status-badge ${status}`}>{labels[status] || status}</span>
}

function scanLabel(status: string | null): string {
  const labels: Record<string, string> = { complete: '已抓完', partial: '只抓到一部分', failed: '本次失败', running: '正在抓取' }
  return status ? (labels[status] || status) : '还没开始'
}

function EmptyState({ title, text }: { title: string; text: string }) {
  return <div className="empty-state"><CircleAlert size={21} /><div><strong>{title}</strong><p>{text}</p></div></div>
}

function label(value: string): string {
  const map: Record<string, string> = {
    research: '研究', algorithm_rd: '算法研发', engineering_infra: '工程/基础设施', application_integration: '应用集成',
    bachelor: '本科', master: '硕士', phd: '博士', unknown: '未知', internship: '实习', campus: '校招', experienced: '社招',
    '0-1y': '0–1年', '2-4y': '2–4年', '5-9y': '5–9年', '10y+': '10年以上',
  }
  return map[value] || value
}

function formatSalaryCny(job: Job): string {
  if (job.annual_cny_lower && job.annual_cny_upper) {
    return `¥${(job.annual_cny_lower / 10_000).toFixed(1)}–${(job.annual_cny_upper / 10_000).toFixed(1)} 万/年`
  }
  if (job.salary_cny_lower && job.salary_cny_upper) {
    const unit = job.salary_period === 'month' ? '月' : job.salary_period === 'day' ? '天' : '年'
    const divisor = unit === '月' && job.salary_cny_lower >= 10_000 ? 1000 : 1
    const suffix = divisor === 1000 ? 'K' : ''
    return `¥${fmt(job.salary_cny_lower / divisor)}–${fmt(job.salary_cny_upper / divisor)}${suffix}/${unit}`
  }
  return job.salary_raw ? '有薪资说明，但无法换算' : '薪资未公开'
}

function formatTime(value: string): string {
  try { return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value)) }
  catch { return value }
}

export default App

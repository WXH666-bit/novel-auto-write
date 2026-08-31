import type {
  CanonItem,
  Chapter,
  PlotThread,
  Project,
  ReviewBundle,
  StoryMap,
} from './types'

export const demoProjects: Project[] = [
  {
    id: 'demo-ink-river',
    title: '雾中灯塔',
    logline: '失去名字的测绘师，在一座会移动的海港里寻找死者留下的潮汐密码。',
    genre: '悬疑 / 奇幻',
    tone: '冷静、潮湿、带一点旧胶片感',
    word_target: 180000,
    chapter_target: 48,
    current_chapter_id: 'demo-ch-03',
    canon_version: 12,
    updated_at: '刚刚',
    cover_mark: '雾',
    source: 'local',
  },
  {
    id: 'demo-snow-letter',
    title: '给雪的信',
    logline: '一封迟到十五年的信，牵出三代人未曾说出口的约定。',
    genre: '现实 / 家庭',
    tone: '克制、明亮、留白',
    word_target: 90000,
    chapter_target: 24,
    current_chapter_id: null,
    canon_version: 4,
    updated_at: '昨天',
    cover_mark: '雪',
    source: 'imported',
  },
]

export const demoChapters: Chapter[] = [
  {
    id: 'demo-ch-01', project_id: 'demo-ink-river', number: 1, title: '潮汐从北面来', volume: 1, volume_title: '潮痕', status: 'accepted', word_count: 3280,
    summary: '林澈在旧港清点失踪的灯具，第一次听见灯塔内部传来的潮声。',
    content: '凌晨四点，北堤的雾还没有散。\n\n林澈把最后一盏煤油灯登记在册，编号七十三。港务处的册页受了潮，墨迹像一群冻死的蚂蚁，挤在纸缝里。\n\n“七十三盏。”他对自己说。\n\n远处的灯塔忽然亮了一下。那座塔已经停摆十二年，塔顶的铜镜早被拆走，谁也说不清那一闪从哪里来。\n\n林澈抬起头，听见雾里有人喊他的名字。喊得很轻，像从水下传来。',
  },
  {
    id: 'demo-ch-02', project_id: 'demo-ink-river', number: 2, title: '没有影子的访客', volume: 1, volume_title: '潮痕', status: 'accepted', word_count: 2950,
    summary: '自称“旧灯守”的访客带来一枚刻着潮线的银币，却没有影子。',
    content: '访客在天亮前抵达。\n\n他穿一件不合时宜的黑色长外套，站在门槛外，鞋尖没有沾上堤岸的泥。\n\n“你在找七十三号灯。”他说。\n\n林澈没有回答。\n\n访客把一枚银币放在窗台上。银币上刻着三道潮线，最深的一道指向灯塔。\n\n“别让它在满月前醒来。”\n\n他说完就走了。晨光照进来时，门外只有一串湿脚印，和一块没有被阳光照到的空地。',
  },
  {
    id: 'demo-ch-03', project_id: 'demo-ink-river', number: 3, title: '把名字交给海', volume: 1, volume_title: '潮痕', status: 'review', word_count: 1864,
    summary: '林澈沿银币的潮线进入封锁的灯塔，发现墙上写满了自己的名字。',
    content: '灯塔的门没有锁。\n\n林澈推开门时，先闻到一股晒干海藻的味道。楼梯向上盘旋，砖缝里长出细白的盐。\n\n他数到第十七级，听见身后有水声。回头时，入口已经不见了，只有一面潮湿的墙。\n\n墙上刻着许多名字。\n\n林澈举起银币，最下面那一行字慢慢渗出黑色的水：\n\n林澈，生于潮历二十七年。\n\n他记得自己生于潮历二十九年。这个念头刚冒出来，头顶便传来铜镜转动的声音。',
  },
  {
    id: 'demo-ch-04', project_id: 'demo-ink-river', number: 4, title: '塔顶的第二轮月', volume: 1, volume_title: '潮痕', status: 'planned', word_count: 0,
    summary: '规划中：塔顶出现第二轮月，林澈必须在记忆改写前做出选择。',
    content: '',
  },
  {
    id: 'demo-ch-05', project_id: 'demo-ink-river', number: 5, title: '盐的证词', volume: 1, volume_title: '潮痕', status: 'planned', word_count: 0,
    summary: '规划中：港务处的旧档案开始自行翻页，失踪者留下了证词。',
    content: '',
  },
]

export const demoCanon: CanonItem[] = [
  { id: 'canon-lin-name', category: 'character', subject: '林澈', predicate: '身份', value: '港务处测绘师；记忆里出生于潮历二十九年', status: 'confirmed', hard: true, aliases: ['小澈'], source_ref: { chapter_id: 'demo-ch-01', chapter_title: '潮汐从北面来', revision_id: 'rev-01', start: 0, end: 88, quote: '林澈把最后一盏煤油灯登记在册' } },
  { id: 'canon-visitor', category: 'character', subject: '旧灯守', predicate: '特征', value: '没有影子；鞋尖不沾泥；只在天亮前出现', status: 'confirmed', hard: true, aliases: ['访客'], source_ref: { chapter_id: 'demo-ch-02', chapter_title: '没有影子的访客', quote: '他的鞋尖没有沾上堤岸的泥' } },
  { id: 'canon-silver', category: 'item', subject: '潮线银币', predicate: '状态', value: '林澈持有；刻有三道潮线；最深潮线指向灯塔', status: 'confirmed', hard: true, source_ref: { chapter_id: 'demo-ch-02', chapter_title: '没有影子的访客', quote: '银币上刻着三道潮线' } },
  { id: 'canon-lighthouse', category: 'world', subject: '北堤灯塔', predicate: '规则', value: '停摆十二年；满月前可能“醒来”；内部空间会改变', status: 'confirmed', hard: true, source_ref: { chapter_id: 'demo-ch-01', chapter_title: '潮汐从北面来', quote: '那座塔已经停摆十二年' } },
  { id: 'canon-birth', category: 'constraint', subject: '林澈的出生年份', predicate: '矛盾', value: '旧墙记录为潮历二十七年，与林澈记忆冲突，尚未定案', status: 'needs_review', hard: true, source_ref: { chapter_id: 'demo-ch-03', chapter_title: '把名字交给海', quote: '林澈，生于潮历二十七年' } },
]

export const demoTimeline = [
  { id: 'time-1', title: '灯塔停摆', date_label: '十二年前', chapter_number: 1, chapter_id: 'demo-ch-01', status: 'past' as const, description: '塔顶铜镜被拆走，港务处封存档案。' },
  { id: 'time-2', title: '七十三号灯失踪', date_label: '昨夜', chapter_number: 1, chapter_id: 'demo-ch-01', status: 'past' as const, description: '林澈完成清点，发现登记与实物不符。' },
  { id: 'time-3', title: '旧灯守现身', date_label: '今晨前', chapter_number: 2, chapter_id: 'demo-ch-02', status: 'past' as const, description: '访客交出潮线银币。' },
  { id: 'time-4', title: '进入灯塔', date_label: '现在', chapter_number: 3, chapter_id: 'demo-ch-03', status: 'current' as const, description: '入口消失，墙上浮出第二个出生年份。' },
  { id: 'time-5', title: '第二轮月升起', date_label: '满月夜', chapter_number: 4, chapter_id: 'demo-ch-04', status: 'planned' as const, description: '下一章必须明确林澈是否继续向上。' },
]

export const demoThreads: PlotThread[] = [
  { id: 'thread-tide', title: '灯塔为何醒来', kind: 'main', status: 'active', color: '#2E7D8C', next_beat: '让第二轮月与出生年份矛盾相互指向', points: [{ chapter_number: 1, label: '异常闪光', state: 'seed' }, { chapter_number: 2, label: '银币指路', state: 'advance' }, { chapter_number: 3, label: '进入塔内', state: 'advance' }, { chapter_number: 4, label: '第二轮月', state: 'payoff' }] },
  { id: 'thread-name', title: '林澈的第二个名字', kind: 'foreshadowing', status: 'active', color: '#C75B4A', next_beat: '暂不解释出生年份，先展示一处可验证的记忆缺口', points: [{ chapter_number: 1, label: '喊名', state: 'seed' }, { chapter_number: 3, label: '墙上名字', state: 'advance' }] },
  { id: 'thread-visitor', title: '没有影子的旧灯守', kind: 'subplot', status: 'dormant', color: '#527362', next_beat: '让他在林澈做选择前再次出现', points: [{ chapter_number: 2, label: '访客', state: 'seed' }] },
]

export const demoStoryMap: StoryMap = { threads: demoThreads, timeline: demoTimeline, characters: demoCanon.filter((item) => item.category === 'character'), foreshadowing: demoCanon.filter((item) => item.category === 'constraint' || item.category === 'item') }

export const demoReview: ReviewBundle = {
  id: 'review-demo-03', project_id: 'demo-ink-river', chapter_id: 'demo-ch-03', revision_id: 'rev-demo-03', status: 'awaiting_review', generated_at: '今天 10:42',
  issues: [
    { id: 'issue-birth', severity: 'critical', type: 'canon_conflict', title: '出生年份与已确认正典冲突', detail: '墙面记录为潮历二十七年，但林澈的已确认状态是潮历二十九年。当前文本没有说明这是伪造、记忆错误还是世界规则。', suggestion: '保留矛盾作为悬念时，请在正典变化中标记“待复核”，并避免后续直接将任一年份当作事实。', source_refs: [{ chapter_id: 'demo-ch-03', chapter_title: '把名字交给海', quote: '林澈，生于潮历二十七年' }, { chapter_id: 'demo-ch-01', chapter_title: '潮汐从北面来', quote: '林澈把最后一盏煤油灯登记在册' }] },
    { id: 'issue-location', severity: 'major', type: 'continuity', title: '灯塔入口状态未交代', detail: '上一章访客在港务处与林澈分开，本章灯塔入口在回头时消失，缺少林澈从港区到北堤的过渡。', suggestion: '补一句时间或移动说明，避免读者误解为空间瞬移。', source_refs: [{ chapter_id: 'demo-ch-02', chapter_title: '没有影子的访客', quote: '访客在天亮前抵达' }] },
    { id: 'issue-style', severity: 'minor', type: 'style', title: '连续三段以“他”开头', detail: '第 4—6 段主语重复，削弱了雾中场景的节奏。', suggestion: '保留一次代词，其他两处改为动作或环境起句。', source_refs: [{ chapter_id: 'demo-ch-03', chapter_title: '把名字交给海', quote: '他记得自己生于潮历二十九年' }] },
  ],
  canon_changes: [
    { id: 'change-birth', action: 'review', item: { id: 'canon-birth-next', category: 'constraint', subject: '林澈的出生年份', predicate: '状态', value: '存在潮历二十七年与二十九年两份记录，暂不判定真伪', status: 'needs_review', hard: true, source_ref: { chapter_id: 'demo-ch-03', chapter_title: '把名字交给海', quote: '林澈，生于潮历二十七年' } }, reason: '本章新证据改变了该事实的确定性。', source_ref: { chapter_id: 'demo-ch-03', chapter_title: '把名字交给海', quote: '林澈，生于潮历二十七年' } },
    { id: 'change-silver', action: 'update', item: { id: 'canon-silver', category: 'item', subject: '潮线银币', predicate: '位置', value: '林澈持有，并在灯塔内作为照明媒介使用', status: 'pending', hard: false, source_ref: { chapter_id: 'demo-ch-03', chapter_title: '把名字交给海', quote: '林澈举起银币' } }, before: demoCanon[2], reason: '正文展示了银币的新用途。', source_ref: { chapter_id: 'demo-ch-03', chapter_title: '把名字交给海', quote: '林澈举起银币' } },
  ],
  source_context: [
    { chapter_id: 'demo-ch-01', chapter_title: '潮汐从北面来', revision_id: 'rev-demo-01', quote: '那座塔已经停摆十二年' },
    { chapter_id: 'demo-ch-02', chapter_title: '没有影子的访客', revision_id: 'rev-demo-02', quote: '别让它在满月前醒来' },
    { chapter_id: 'demo-ch-03', chapter_title: '把名字交给海', revision_id: 'rev-demo-03', quote: '林澈，生于潮历二十七年' },
  ],
  blocking_count: 1,
}

export const demoProvider = {
  name: 'Demo Provider', base_url: 'http://127.0.0.1:11434/v1', protocol: 'demo' as const, default_model: 'demo-writer', context_length: 32768, timeout_ms: 60000, is_demo: true, api_key_set: false,
  model_roles: { planner: 'local-storyteller', drafter: 'local-storyteller', auditor: 'local-storyteller' }, capabilities: { streaming: true, structured_outputs: false },
}

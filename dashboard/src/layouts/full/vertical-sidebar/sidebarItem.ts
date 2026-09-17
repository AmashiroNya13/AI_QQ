export interface menu {
  header?: string;
  title?: string;
  icon?: string;
  iconSize?: string | number;
  to?: string;
  divider?: boolean;
  chip?: string;
  chipColor?: string;
  chipVariant?: string;
  chipIcon?: string;
  children?: menu[];
  disabled?: boolean;
  type?: string;
  subCaption?: string;
  isRawTitle?: boolean;
}

export const MORE_GROUP_KEY = 'ecobot.navigation.more';

const sidebarItem: menu[] = [
  { title: '控制台', icon: 'mdi-view-dashboard-outline', to: '/welcome', isRawTitle: true },
  {
    title: '接入与模型', icon: 'mdi-connection', children: [
      { title: 'QQ 接入', icon: 'mdi-robot-outline', to: '/platforms' },
      { title: '模型服务', icon: 'mdi-creation-outline', to: '/providers' },
      { title: '运行配置', icon: 'mdi-tune-variant', to: '/config' },
    ],
  },
  {
    title: '人格与成长', icon: 'mdi-account-heart-outline', children: [
      { title: '人格设定', icon: 'mdi-account-heart-outline', to: '/persona' },
      { title: '语气与身份学习', icon: 'mdi-message-text-outline', to: '/ecobot/style' },
    ],
  },
  {
    title: '当前生活', icon: 'mdi-earth', children: [
      { title: '人物状态', icon: 'mdi-account-clock-outline', to: '/ecobot/state' },
      { title: '世界与场景', icon: 'mdi-earth', to: '/ecobot/world' },
      { title: '自治闭环', icon: 'mdi-autorenew', to: '/ecobot/autonomy' },
    ],
  },
  {
    title: '对话与行为', icon: 'mdi-timeline-text-outline', children: [
      { title: '思考与行为', icon: 'mdi-timeline-text-outline', to: '/ecobot/runs' },
      { title: 'AI 介入控制台', icon: 'mdi-tune', to: '/ecobot/settings' },
    ],
  },
  {
    title: '人物与关系', icon: 'mdi-account-multiple-outline', children: [
      { title: '人物关系', icon: 'mdi-account-multiple-outline', to: '/ecobot/relationships' },
      { title: 'QQ 数据档案', icon: 'mdi-database-search-outline', to: '/ecobot/archive' },
    ],
  },
  { title: '记忆系统', icon: 'mdi-brain', to: '/ecobot/memory', isRawTitle: true },
  { title: '日志与调试', icon: 'mdi-text-box-search-outline', to: '/data/logs', isRawTitle: true },
];

export default sidebarItem;

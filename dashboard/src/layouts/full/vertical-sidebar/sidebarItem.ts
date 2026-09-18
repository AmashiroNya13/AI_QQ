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
    title: '接入与模型', icon: 'mdi-connection', isRawTitle: true, children: [
      { title: 'QQ 接入', icon: 'mdi-robot-outline', to: '/platforms', isRawTitle: true },
      { title: '模型服务', icon: 'mdi-creation-outline', to: '/providers', isRawTitle: true },
      { title: '运行配置', icon: 'mdi-tune-variant', to: '/config', isRawTitle: true },
    ],
  },
  {
    title: '人格与成长', icon: 'mdi-account-heart-outline', isRawTitle: true, children: [
      { title: '人格设定', icon: 'mdi-account-heart-outline', to: '/persona', isRawTitle: true },
    ],
  },
  {
    title: '当前生活', icon: 'mdi-earth', isRawTitle: true, children: [
      { title: '人物状态', icon: 'mdi-account-clock-outline', to: '/ecobot/state', isRawTitle: true },
      { title: '世界与场景', icon: 'mdi-earth', to: '/ecobot/world', isRawTitle: true },
      { title: '自治闭环', icon: 'mdi-autorenew', to: '/ecobot/autonomy', isRawTitle: true },
    ],
  },
  {
    title: '对话与行为', icon: 'mdi-timeline-text-outline', isRawTitle: true, children: [
      { title: '主体决策记录', icon: 'mdi-timeline-text-outline', to: '/ecobot/autonomy', isRawTitle: true },
      { title: 'AI 介入控制台', icon: 'mdi-tune', to: '/ecobot/settings', isRawTitle: true },
    ],
  },
  {
    title: '人物与关系', icon: 'mdi-account-multiple-outline', isRawTitle: true, children: [
      { title: '人物关系', icon: 'mdi-account-multiple-outline', to: '/ecobot/relationships', isRawTitle: true },
      { title: 'QQ 数据档案', icon: 'mdi-database-search-outline', to: '/ecobot/archive', isRawTitle: true },
    ],
  },
  { title: '记忆系统', icon: 'mdi-brain', to: '/ecobot/memory', isRawTitle: true },
  { title: '日志与调试', icon: 'mdi-text-box-search-outline', to: '/data/logs', isRawTitle: true },
];

export default sidebarItem;

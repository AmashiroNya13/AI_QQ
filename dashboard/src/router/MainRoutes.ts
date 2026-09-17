import type { RouteLocationNormalized } from 'vue-router';

const redirectToDataTab = (name: string) => (to: RouteLocationNormalized) => ({
  name,
  query: to.query,
  hash: to.hash
});

const MainRoutes = {
  path: '/main',
  meta: {
    requiresAuth: true
  },
  redirect: '/welcome',
  component: () => import('@/layouts/full/FullLayout.vue'),
  children: [
    {
      name: 'MainPage',
      path: '/',
      component: () => import('@/views/ecobot/EcobotOverviewPage.vue')
    },
    {
      name: 'Welcome',
      path: '/welcome',
      component: () => import('@/views/ecobot/EcobotOverviewPage.vue')
    },
    {
      name: 'Platforms',
      path: '/platforms',
      component: () => import('@/views/PlatformPage.vue')
    },
    {
      name: 'Providers',
      path: '/providers',
      component: () => import('@/views/ProviderPage.vue')
    },
    {
      name: 'EcobotState',
      path: '/ecobot/state',
      component: () => import('@/views/ecobot/EcobotStatePage.vue')
    },
    {
      name: 'EcobotWorld',
      path: '/ecobot/world',
      component: () => import('@/views/ecobot/EcobotWorldPage.vue')
    },
    {
      name: 'EcobotMemory',
      path: '/ecobot/memory',
      component: () => import('@/views/ecobot/EcobotMemoryPage.vue')
    },
    {
      name: 'EcobotStyle',
      path: '/ecobot/style',
      component: () => import('@/views/ecobot/EcobotStylePage.vue')
    },
    {
      name: 'EcobotAutonomy',
      path: '/ecobot/autonomy',
      component: () => import('@/views/ecobot/EcobotAutonomyPage.vue')
    },
    {
      name: 'EcobotRelationships',
      path: '/ecobot/relationships',
      component: () => import('@/views/ecobot/EcobotRelationshipsPage.vue')
    },
    {
      name: 'EcobotArchive',
      path: '/ecobot/archive',
      component: () => import('@/views/ecobot/EcobotArchivePage.vue')
    },
    {
      name: 'EcobotRuns',
      path: '/ecobot/runs',
      component: () => import('@/views/ecobot/EcobotRunsPage.vue')
    },
    {
      name: 'EcobotSettings',
      path: '/ecobot/settings',
      component: () => import('@/views/ecobot/EcobotSettingsPage.vue')
    },
    {
      name: 'Configs',
      path: '/config',
      component: () => import('@/views/ConfigPage.vue')
    },
    {
      path: '/normal',
      redirect: '/config'
    },
    {
      path: '/system',
      redirect: '/settings#system-config'
    },
    {
      name: 'Persona',
      path: '/persona',
      component: () => import('@/views/PersonaPage.vue')
    },
    {
      name: 'Data',
      path: '/data',
      component: () => import('@/views/DataPage.vue'),
      redirect: redirectToDataTab('Stats'),
      children: [
        {
          name: 'Stats',
          path: 'statistics',
          component: () => import('@/views/stats/StatsPage.vue'),
          meta: { dataTab: 'statistics' }
        },
        {
          name: 'Conversation',
          path: 'conversations',
          component: () => import('@/views/conversation/ConversationWorkspacePage.vue'),
          meta: { dataTab: 'conversations' }
        },
        {
          name: 'ConversationLegacy',
          path: 'conversations/legacy',
          component: () => import('@/views/conversation/LegacyConversationPage.vue'),
          meta: { dataTab: 'conversations' }
        },
        {
          name: 'Console',
          path: 'logs',
          component: () => import('@/views/ConsolePage.vue'),
          meta: { dataTab: 'logs' }
        },
        {
          name: 'Trace',
          path: 'trace',
          component: () => import('@/views/TracePage.vue'),
          meta: { dataTab: 'trace' }
        }
      ]
    },
    {
      path: '/dashboard/default',
      redirect: redirectToDataTab('Stats')
    },
    {
      path: '/conversation',
      redirect: redirectToDataTab('Conversation')
    },
    {
      path: '/console',
      redirect: redirectToDataTab('Console')
    },
    {
      path: '/trace',
      redirect: redirectToDataTab('Trace')
    },
    {
      path: '/observability',
      redirect: redirectToDataTab('Stats')
    },
    {
      name: 'Chat',
      path: '/chat',
      component: () => import('@/views/ChatPage.vue'),
      children: [
        {
          path: ':conversationId',
          name: 'ChatDetail',
          component: () => import('@/views/ChatPage.vue'),
          props: true
        }
      ]
    },
    {
      name: 'Settings',
      path: '/settings',
      component: () => import('@/views/Settings.vue')
    },
    {
      name: 'About',
      path: '/about',
      component: () => import('@/views/AboutPage.vue')
    }
  ]
};

export default MainRoutes;

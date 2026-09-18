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
      name: 'Persona',
      path: '/persona',
      component: () => import('@/views/PersonaPage.vue')
    },
    {
      name: 'EcobotLogs',
      path: '/data/logs',
      component: () => import('@/views/ConsolePage.vue')
    },
    {
      name: 'EcobotTrace',
      path: '/data/trace',
      component: () => import('@/views/TracePage.vue')
    },
  ]
};

export default MainRoutes;

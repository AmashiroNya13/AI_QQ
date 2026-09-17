// 静态导入所有翻译文件
// 这种方式确保构建时所有翻译都会被正确打包

// 中文翻译
import zhCNCommon from './locales/zh-CN/core/common.json';
import zhCNActions from './locales/zh-CN/core/actions.json';
import zhCNStatus from './locales/zh-CN/core/status.json';
import zhCNNavigation from './locales/zh-CN/core/navigation.json';
import zhCNHeader from './locales/zh-CN/core/header.json';
import zhCNShared from './locales/zh-CN/core/shared.json';

import zhCNProvider from './locales/zh-CN/features/provider.json';
import zhCNPlatform from './locales/zh-CN/features/platform.json';
import zhCNConfig from './locales/zh-CN/features/config.json';
import zhCNConfigMetadata from './locales/zh-CN/features/config-metadata.json';
import zhCNConsole from './locales/zh-CN/features/console.json';
import zhCNTrace from './locales/zh-CN/features/trace.json';
import zhCNAuth from './locales/zh-CN/features/auth.json';
import zhCNDashboard from './locales/zh-CN/features/dashboard.json';
import zhCNPersona from './locales/zh-CN/features/persona.json';

import zhCNErrors from './locales/zh-CN/messages/errors.json';
import zhCNSuccess from './locales/zh-CN/messages/success.json';
import zhCNValidation from './locales/zh-CN/messages/validation.json';

// English translation
import enUSCommon from './locales/en-US/core/common.json';
import enUSActions from './locales/en-US/core/actions.json';
import enUSStatus from './locales/en-US/core/status.json';
import enUSNavigation from './locales/en-US/core/navigation.json';
import enUSHeader from './locales/en-US/core/header.json';
import enUSShared from './locales/en-US/core/shared.json';

import enUSProvider from './locales/en-US/features/provider.json';
import enUSPlatform from './locales/en-US/features/platform.json';
import enUSConfig from './locales/en-US/features/config.json';
import enUSConfigMetadata from './locales/en-US/features/config-metadata.json';
import enUSConsole from './locales/en-US/features/console.json';
import enUSTrace from './locales/en-US/features/trace.json';
import enUSAuth from './locales/en-US/features/auth.json';
import enUSDashboard from './locales/en-US/features/dashboard.json';
import enUSPersona from './locales/en-US/features/persona.json';

import enUSErrors from './locales/en-US/messages/errors.json';
import enUSSuccess from './locales/en-US/messages/success.json';
import enUSValidation from './locales/en-US/messages/validation.json';

// Russian translation
import ruRUCommon from './locales/ru-RU/core/common.json';
import ruRUActions from './locales/ru-RU/core/actions.json';
import ruRUStatus from './locales/ru-RU/core/status.json';
import ruRUNavigation from './locales/ru-RU/core/navigation.json';
import ruRUHeader from './locales/ru-RU/core/header.json';
import ruRUShared from './locales/ru-RU/core/shared.json';

import ruRUProvider from './locales/ru-RU/features/provider.json';
import ruRUPlatform from './locales/ru-RU/features/platform.json';
import ruRUConfig from './locales/ru-RU/features/config.json';
import ruRUConfigMetadata from './locales/ru-RU/features/config-metadata.json';
import ruRUConsole from './locales/ru-RU/features/console.json';
import ruRUTrace from './locales/ru-RU/features/trace.json';
import ruRUAuth from './locales/ru-RU/features/auth.json';
import ruRUDashboard from './locales/ru-RU/features/dashboard.json';
import ruRUPersona from './locales/ru-RU/features/persona.json';

import ruRUErrors from './locales/ru-RU/messages/errors.json';
import ruRUSuccess from './locales/ru-RU/messages/success.json';
import ruRUValidation from './locales/ru-RU/messages/validation.json';

// Japanese translation
import jaJPCommon from './locales/ja-JP/core/common.json';
import jaJPActions from './locales/ja-JP/core/actions.json';
import jaJPStatus from './locales/ja-JP/core/status.json';
import jaJPNavigation from './locales/ja-JP/core/navigation.json';
import jaJPHeader from './locales/ja-JP/core/header.json';
import jaJPShared from './locales/ja-JP/core/shared.json';

import jaJPProvider from './locales/ja-JP/features/provider.json';
import jaJPPlatform from './locales/ja-JP/features/platform.json';
import jaJPConfig from './locales/ja-JP/features/config.json';
import jaJPConfigMetadata from './locales/ja-JP/features/config-metadata.json';
import jaJPConsole from './locales/ja-JP/features/console.json';
import jaJPTrace from './locales/ja-JP/features/trace.json';
import jaJPAuth from './locales/ja-JP/features/auth.json';
import jaJPDashboard from './locales/ja-JP/features/dashboard.json';
import jaJPPersona from './locales/ja-JP/features/persona.json';

import jaJPErrors from './locales/ja-JP/messages/errors.json';
import jaJPSuccess from './locales/ja-JP/messages/success.json';
import jaJPValidation from './locales/ja-JP/messages/validation.json';

// 组装翻译对象
export const translations = {
  'zh-CN': {
    core: {
      common: zhCNCommon,
      actions: zhCNActions,
      status: zhCNStatus,
      navigation: zhCNNavigation,
      header: zhCNHeader,
      shared: zhCNShared
    },
    features: {
      provider: zhCNProvider,
      platform: zhCNPlatform,
      config: zhCNConfig,
      'config-metadata': zhCNConfigMetadata,
      console: zhCNConsole,
      trace: zhCNTrace,
      auth: zhCNAuth,
      dashboard: zhCNDashboard,
      persona: zhCNPersona
    },
    messages: {
      errors: zhCNErrors,
      success: zhCNSuccess,
      validation: zhCNValidation
    }
  },
  'en-US': {
    core: {
      common: enUSCommon,
      actions: enUSActions,
      status: enUSStatus,
      navigation: enUSNavigation,
      header: enUSHeader,
      shared: enUSShared
    },
    features: {
      provider: enUSProvider,
      platform: enUSPlatform,
      config: enUSConfig,
      'config-metadata': enUSConfigMetadata,
      console: enUSConsole,
      trace: enUSTrace,
      auth: enUSAuth,
      dashboard: enUSDashboard,
      persona: enUSPersona
    },
    messages: {
      errors: enUSErrors,
      success: enUSSuccess,
      validation: enUSValidation
    }
  },
  'ru-RU': {
    core: {
      common: ruRUCommon,
      actions: ruRUActions,
      status: ruRUStatus,
      navigation: ruRUNavigation,
      header: ruRUHeader,
      shared: ruRUShared
    },
    features: {
      provider: ruRUProvider,
      platform: ruRUPlatform,
      config: ruRUConfig,
      'config-metadata': ruRUConfigMetadata,
      console: ruRUConsole,
      trace: ruRUTrace,
      auth: ruRUAuth,
      dashboard: ruRUDashboard,
      persona: ruRUPersona
    },
    messages: {
      errors: ruRUErrors,
      success: ruRUSuccess,
      validation: ruRUValidation
    }
  },
  'ja-JP': {
    core: {
      common: jaJPCommon,
      actions: jaJPActions,
      status: jaJPStatus,
      navigation: jaJPNavigation,
      header: jaJPHeader,
      shared: jaJPShared
    },
    features: {
      provider: jaJPProvider,
      platform: jaJPPlatform,
      config: jaJPConfig,
      'config-metadata': jaJPConfigMetadata,
      console: jaJPConsole,
      trace: jaJPTrace,
      auth: jaJPAuth,
      dashboard: jaJPDashboard,
      persona: jaJPPersona
    },
    messages: {
      errors: jaJPErrors,
      success: jaJPSuccess,
      validation: jaJPValidation
    }
  }
};

export type TranslationData = typeof translations; 

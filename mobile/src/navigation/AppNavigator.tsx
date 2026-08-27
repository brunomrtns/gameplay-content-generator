import React from 'react';
import { NavigationContainer, DarkTheme } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import Icon from 'react-native-vector-icons/MaterialCommunityIcons';
import { colors } from '../theme/colors';
import { useAuth } from '../hooks/useAuth';
import { LoginScreen } from '../screens/LoginScreen';
import { LandingScreen } from '../screens/LandingScreen';
import { DashboardScreen } from '../screens/DashboardScreen';
import { JobsScreen } from '../screens/JobsScreen';
import { VideosScreen } from '../screens/VideosScreen';
import { ContentScreen } from '../screens/ContentScreen';
import { AutomationScreen } from '../screens/AutomationScreen';
import { IdeasScreen } from '../screens/IdeasScreen';
import { KidsScreen } from '../screens/KidsScreen';
import { KidsIdeasScreen } from '../screens/KidsIdeasScreen';
import { AdminScreen } from '../screens/AdminScreen';
import { LoadingScreen } from '../screens/LoadingScreen';
import { MoreScreen } from '../screens/MoreScreen';
import { useAppUpdate } from '../hooks/useAppUpdate';
import { UpdateBanner } from '../components/UpdateBanner';
import { View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

export type RootStackParamList = {
  Landing: undefined;
  Login: undefined;
  Main: undefined;
};

export type MainTabParamList = {
  Dashboard: undefined;
  Conteudo: undefined;
  Ideias: undefined;
  Videos: undefined;
  Mais: undefined;
};

export type MoreStackParamList = {
  MoreHome: undefined;
  Automacao: undefined;
  Jobs: undefined;
  Kids: undefined;
  KidsIdeas: undefined;
  Admin: undefined;
};

const Stack = createNativeStackNavigator<RootStackParamList>();
const Tab = createBottomTabNavigator<MainTabParamList>();
const MoreStack = createNativeStackNavigator<MoreStackParamList>();

const navTheme = {
  ...DarkTheme,
  colors: {
    ...DarkTheme.colors,
    background: colors.bg,
    card: colors.surface,
    text: colors.text,
    border: colors.border,
    primary: colors.accent,
  },
};

function MainTabs() {
  return (
    <Tab.Navigator
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopColor: colors.border,
          borderTopWidth: 1,
        },
      }}
    >
      <Tab.Screen
        name="Dashboard"
        component={DashboardScreen}
        options={{
          tabBarIcon: ({ color, size }) => <Icon name="view-dashboard" color={color} size={size} />,
          tabBarLabel: 'Início',
        }}
      />
      <Tab.Screen
        name="Conteudo"
        component={ContentScreen}
        options={{
          tabBarIcon: ({ color, size }) => <Icon name="film" color={color} size={size} />,
          tabBarLabel: 'Conteúdo',
        }}
      />
      <Tab.Screen
        name="Ideias"
        component={IdeasScreen}
        options={{
          tabBarIcon: ({ color, size }) => <Icon name="lightbulb-on" color={color} size={size} />,
          tabBarLabel: 'Ideias',
        }}
      />
      <Tab.Screen
        name="Videos"
        component={VideosScreen}
        options={{
          tabBarIcon: ({ color, size }) => <Icon name="video" color={color} size={size} />,
          tabBarLabel: 'Vídeos',
        }}
      />
      <Tab.Screen
        name="Mais"
        component={MoreStackScreen}
        options={{
          tabBarIcon: ({ color, size }) => <Icon name="menu" color={color} size={size} />,
          tabBarLabel: 'Mais',
        }}
      />
    </Tab.Navigator>
  );
}

function MoreStackScreen() {
  const { user, logout } = useAuth();
  return (
    <MoreStack.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: colors.surface },
        headerTintColor: colors.text,
        headerShadowVisible: false,
      }}
    >
      <MoreStack.Screen
        name="MoreHome"
        options={{ title: 'Mais', headerShown: false }}
      >
        {(props: any) => <MoreScreen {...props} user={user} onLogout={logout} />}
      </MoreStack.Screen>
      <MoreStack.Screen
        name="Automacao"
        component={AutomationScreen}
        options={{ title: 'Automação', headerShown: false }}
      />
      <MoreStack.Screen
        name="Jobs"
        component={JobsScreen}
        options={{ title: 'Jobs', headerShown: false }}
      />
      <MoreStack.Screen
        name="Kids"
        component={KidsScreen}
        options={{ title: 'Kids', headerShown: false }}
      />
      <MoreStack.Screen
        name="KidsIdeas"
        component={KidsIdeasScreen}
        options={{ title: 'Ideias Kids', headerShown: false }}
      />
      {user?.is_admin && (
        <MoreStack.Screen
          name="Admin"
          component={AdminScreen}
          options={{ title: 'Admin', headerShown: false }}
        />
      )}
    </MoreStack.Navigator>
  );
}

export function AppNavigator() {
  const { user, loading, isAuthenticated } = useAuth();
  const { updateInfo, dismissed, openDownloadPage, dismiss } = useAppUpdate();

  if (loading) return <LoadingScreen />;

  const showBanner = updateInfo?.hasUpdate && !dismissed;

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      {showBanner && updateInfo && (
        <SafeAreaView edges={['top']} style={{ backgroundColor: colors.surface }}>
          <UpdateBanner
            updateInfo={updateInfo}
            onDownload={openDownloadPage}
            onDismiss={dismiss}
          />
        </SafeAreaView>
      )}
      <NavigationContainer theme={navTheme}>
        <Stack.Navigator screenOptions={{ headerShown: false }}>
          {isAuthenticated ? (
            <Stack.Screen name="Main" component={MainTabs} />
          ) : (
            <>
              <Stack.Screen name="Landing" component={LandingScreen} />
              <Stack.Screen name="Login" component={LoginScreen} />
            </>
          )}
        </Stack.Navigator>
      </NavigationContainer>
    </View>
  );
}

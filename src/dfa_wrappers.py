import numpy as np
import gym
from gym import spaces
from copy import deepcopy
import random
import dfa_samplers
# from envs.safety.zones_env import zone
import networkx as nx
import pickle
from dfa import DFA
import math

class DFAEnv(gym.Wrapper):
    def __init__(self, env, dfa_sampler=None, reject_reward=0):
        super().__init__(env)
        self.propositions = self.env.get_propositions()
        self.sampler = dfa_samplers.getDFASampler(dfa_sampler, self.propositions)

        # self.num_states_upper = 6
        # Q = self.num_states_upper
        # m = Q * (Q-1) / 2
        # F = self.num_states_upper - 1
        Q = self.sampler.get_n_states()
        F = self.sampler.get_n_accepting_states()
        E = self.sampler.get_n_alphabet()
        m = self.sampler.get_n_transitions()

        b_Q = math.ceil(math.log(Q, 2))
        b_E = math.ceil(math.log(E, 2))

        self.N = math.ceil(3 + 2*b_Q + 2*b_E + (F + 1)*b_Q + m*(b_E + 2*b_Q) + 1)

        self.observation_space = spaces.Dict({"features": env.observation_space,
                                              "dfa"     : spaces.MultiBinary(self.N)})

        self.dfa_goal_int = None
        self.dfa_goal_binary_seq = None

        self.reject_reward = reject_reward

    @property
    def dfa_goal(self):
        return DFA.from_int(self.dfa_goal_int, self.propositions)

    def sample_dfa_goal(self):
        # This function must return a DFA for a task.
        # Format: networkx graph
        # NOTE: The propositions must be represented by a char
        # dfa = self.sampler.sample()
        from dfa import DFA
        dfa = self.sampler.sample_dfa_formula()
        # print("DFA", type(dfa), dfa)
        return dfa

    def get_events(self, obs, act, next_obs):
        # This function must return the events that currently hold on the environment
        # NOTE: The events are represented by a string containing the propositions with positive values only (e.g., "ac" means that only propositions 'a' and 'b' hold)
        return self.env.get_events()

    def get_binary_seq(self, dfa):
        binary_string = bin(dfa.to_int())[2:]
        binary_seq = np.array([int(i) for i in binary_string])
        return np.pad(binary_seq, (self.N - binary_seq.shape[0], 0), 'constant', constant_values=(0, 0))

    def reset(self):
        self.known_progressions = {}
        self.obs = self.env.reset()
        self.dfa_goal_int = self.sample_dfa_goal().to_int()
        self.dfa_goal_binary_seq = self.get_binary_seq(self.dfa_goal)
        dfa_obs = {"features": self.obs, "dfa": self.dfa_goal_binary_seq}
        return dfa_obs

    def get_reward_and_done(self):
        dfa_reward = 0.0
        dfa_done = False

        start_state = self.dfa_goal.start
        start_state_label = self.dfa_goal._label(start_state)
        states = self.dfa_goal.states()
        if start_state_label == True: # If starting state of self.dfa_goal is accepting, then dfa_reward is 1.0.
            dfa_reward = 1.0
            dfa_done = True
        elif len(states) == 1: # If starting state of self.dfa_goal is rejecting and self.dfa_goal has a single state, then dfa_reward is reject_reward.
            dfa_reward = self.reject_reward
            dfa_done = True
        else:
            dfa_reward = 0.0 # If starting state of self.dfa_goal is rejecting and self.dfa_goal has a multiple states, then dfa_reward is 0.0.
            dfa_done = False
        return dfa_reward, dfa_done

    def get_reward_and_done_given_dfa(self, dfa):
        dfa_reward = 0.0
        dfa_done = False

        start_state = dfa.start
        start_state_label = dfa._label(start_state)
        states = dfa.states()
        if start_state_label == True: # If starting state of self.dfa_goal is accepting, then dfa_reward is 1.0.
            dfa_reward = 1.0
            dfa_done = True
        elif len(states) == 1: # If starting state of self.dfa_goal is rejecting and self.dfa_goal has a single state, then dfa_reward is reject_reward.
            dfa_reward = self.reject_reward
            dfa_done = True
        else:
            dfa_reward = 0.0 # If starting state of self.dfa_goal is rejecting and self.dfa_goal has a multiple states, then dfa_reward is 0.0.
            dfa_done = False
        return dfa_reward, dfa_done

    def get_dfa_from_binary_seq(self, dfa_binary_seq):
        dfa_binary_str = "".join(str(int(i)) for i in dfa_binary_seq.squeeze().tolist())
        dfa_int = int(dfa_binary_str, 2)
        dfa = DFA.from_int(dfa_int, self.propositions)
        return dfa

    def step(self, action):
        # executing the action in the environment

        next_obs, original_reward, env_done, info = self.env.step(action)

        truth_assignment = self.get_events(self.obs, action, next_obs)
        next_dfa_goal = self.dfa_goal.advance(truth_assignment)
        if next_dfa_goal != self.dfa_goal:
            next_dfa_goal = next_dfa_goal.minimize()
            self.dfa_goal_int = next_dfa_goal.to_int()
            self.dfa_goal_binary_seq = self.get_binary_seq(self.dfa_goal)

        dfa_reward, dfa_done = self.get_reward_and_done()

        self.obs = next_obs

        dfa_obs = {'features': self.obs, 'dfa': self.dfa_goal_binary_seq}

        reward  = original_reward + dfa_reward
        done    = env_done or dfa_done

        return dfa_obs, reward, done, info


    def step_given_obs(self, obs, action, time):

        # advance the environment
        next_feature, original_reward, env_done, info = self.env.step_from_obs(obs["features"], action, time)

        # advance the dfa
        dfa_bin_seq = obs["dfa"]
        dfa = self.get_dfa_from_binary_seq(dfa_bin_seq)
        truth_assignment = self.env.get_events_given_obs(next_feature)
        next_dfa = dfa.advance(truth_assignment)


        if next_dfa != dfa:
            next_dfa = next_dfa.minimize()
            next_dfa_bin_seq = np.expand_dims(self.get_binary_seq(next_dfa), axis=0)

        else:
            next_dfa_bin_seq = dfa_bin_seq

        next_obs = {'features': next_feature, 'dfa': next_dfa_bin_seq}

        dfa_reward, dfa_done = self.get_reward_and_done_given_dfa(next_dfa)
        reward  = original_reward + dfa_reward
        done    = env_done or dfa_done


        return next_obs, reward, done, info

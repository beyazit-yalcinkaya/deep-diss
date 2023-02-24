import asyncio
import argparse
from utils import make_env
from stable_baselines3 import DQN
from stable_baselines3.common.env_checker import check_env
from feature_extractor import CustomCombinedExtractor
from stable_baselines3.common.off_policy_algorithm import OffPolicyAlgorithm
from stable_baselines3.common.callbacks import BaseCallback, CallbackList, ConvertCallback, ProgressBarCallback

from stable_baselines3.common.type_aliases import MaybeCallback

from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar, Union
from stable_baselines3.common.type_aliases import GymEnv, MaybeCallback, RolloutReturn, Schedule, TrainFreq, TrainFrequencyUnit

from diss_relabeler import DissRelabeler
from diss_replay_buffer import DissReplayBuffer

class DiscountedRewardCallback(BaseCallback):
    """
    Custom callback for plotting additional values in tensorboard.
    """

    def __init__(self, gamma, verbose=0):
        self.gamma = gamma
        self.discounted_return_sum = 0.0
        self.num_episodes = 1

        super(DiscountedRewardCallback, self).__init__(verbose)

    def _on_step(self):
        # Log scalar value (here a random variable)
        dones = self.locals["dones"]
        infos = self.locals["infos"]

        for idx, done in enumerate(dones):
            info = infos[idx]
            if (
                done
                and info.get("episode") is not None
            ):
                episode_info = info["episode"]
                discounted_return = episode_info['r'] * self.gamma ** episode_info['l']
                self.discounted_return_sum += discounted_return
                self.num_episodes += 1


        return True

    def _on_rollout_end(self):
        ep_disc_rew_mean = self.discounted_return_sum/self.num_episodes
        self.logger.record("rollout/ep_disc_rew_mean", ep_disc_rew_mean)
        discounted_return_sum = 0.0


async def relabel(relabeler, relabeler_name, batch_size):
    if relabeler_name == "diss":
        relabeler.relabel_diss(batch_size)
    elif relabeler_name == "baseline":
        relabeler.relabel_baseline(batch_size)

async def learn(
    model: OffPolicyAlgorithm,
    total_timesteps: int,
    callback: MaybeCallback = None,
    log_interval: int = 4,
    eval_env: Optional[GymEnv] = None,
    eval_freq: int = -1, 
    n_eval_episodes: int = 5,
    tb_log_name: str = "run",
    eval_log_path: Optional[str] = None,
    reset_num_timesteps: bool = True,
    progress_bar: bool = False,
):

    rollout = model.collect_rollouts(
        model.env,
        train_freq=model.train_freq,
        action_noise=model.action_noise,
        callback=callback,
        learning_starts=model.learning_starts,
        replay_buffer=model.replay_buffer,
        log_interval=log_interval,
    )

    if rollout.continue_training is False:
        return True, model

    if model.num_timesteps > 0 and model.num_timesteps > model.learning_starts:
        # If no `gradient_steps` is specified,
        # do as many gradients steps as steps performed during the rollout
        gradient_steps = model.gradient_steps if model.gradient_steps >= 0 else rollout.episode_timesteps
        # Special case when the user passes `gradient_steps=0`
        if gradient_steps > 0:
            model.train(batch_size=model.batch_size, gradient_steps=gradient_steps)

    return False, model

async def learn_with_diss(
    model: OffPolicyAlgorithm,
    env,
    relabeler_name,
    save_file_name,
    total_timesteps: int,
    callback: MaybeCallback = None,
    log_interval: int = 4,
    eval_env: Optional[GymEnv] = None,
    eval_freq: int = -1, 
    n_eval_episodes: int = 5,
    tb_log_name: str = "run",
    eval_log_path: Optional[str] = None,
    reset_num_timesteps: bool = True,
    progress_bar: bool = False,
):
    relabeler = DissRelabeler(model, env)

    total_timesteps, callback = model._setup_learn(
        total_timesteps,
        eval_env,
        callback,
        eval_freq,
        n_eval_episodes,
        eval_log_path,
        reset_num_timesteps,
        tb_log_name,
        progress_bar,
    )

    callback.on_training_start(locals(), globals())

    while model.num_timesteps < total_timesteps:

        task1 = asyncio.create_task(learn(model, total_timesteps=10000000, callback=callback))
        task2 = asyncio.create_task(relabel(relabeler, relabeler_name, 2))

        await task1
        await task2

        done, model = task1.result()
        if done:
            break

    callback.on_training_end()

    model.save(save_file_name)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument("--env", required=True,
                            help="name of the environment to train on (REQUIRED)")
    parser.add_argument("--sampler", default="Default",
                        help="the ltl formula template to sample from (default: DefaultSampler)")
    parser.add_argument("--relabeler", default="diss",
                        help="baseline | diss")
    parser.add_argument("--seed", type=int, default=1,
                            help="random seed (default: 1)")

    args = parser.parse_args()

    env = make_env(args.env, args.sampler, seed=args.seed)

    print("------------------------------------------------")
    print(env)
    check_env(env)
    print("------------------------------------------------")

    gamma = 0.94

    discounted_reward_callback = DiscountedRewardCallback(gamma)


    if args.relabeler == 'none':
        model = DQN(
            "MultiInputPolicy",
            env,
            policy_kwargs=dict(
                features_extractor_class=CustomCombinedExtractor,
                features_extractor_kwargs=dict(env=env)
                ),
            verbose=1,
            tensorboard_log="./depth9_tensorboard/no_relabel",
            learning_starts=50000,
            batch_size=10,
            gamma=gamma
            )
        model.learn(total_timesteps=3000000, callback=discounted_reward_callback)
    else:
        model = DQN(
            "MultiInputPolicy",
            env,
            policy_kwargs=dict(
                features_extractor_class=CustomCombinedExtractor,
                features_extractor_kwargs=dict(env=env)
                ),
            replay_buffer_class=DissReplayBuffer,
            replay_buffer_kwargs=dict(
                max_episode_length=env.timeout,
                her_replay_buffer_size=5000
                ),
            learning_starts=50000,
            batch_size=10,
            gamma=gamma,
            verbose=1,
            tensorboard_log="./depth9_tensorboard/baseline_relabel"
            )

        asyncio.run(learn_with_diss(model, env, args.relabeler, "dqn", callback=discounted_reward_callback, total_timesteps=3000000))




import sys

from omegaconf import DictConfig
import hydra
import torch
from tqdm import tqdm

from curiosity.experience import Transition
from curiosity.experience.util import build_collector, build_replay_buffer
from curiosity.policy import ColoredNoisePolicy
from curiosity.rl.ddpg import DeepDeterministicPolicyGradient
from curiosity.util import build_env, build_actor, build_critic, global_seed
from curiosity.logging import CuriosityEvaluator, CuriosityLogger

DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

@hydra.main(version_base=None, config_path="../config", config_name="defaults")
def train(cfg: DictConfig) -> None:
    # Environment
    env = build_env(cfg.env)

    # Logging and Evaluation
    logger, evaluator = CuriosityLogger(cfg, "ddpg"), CuriosityEvaluator(env, device=DEVICE, **cfg.log.evaluation)

    # RNG
    rng = global_seed(cfg.seed, env, evaluator)

    # Define actor-critic policy
    algorithm = DeepDeterministicPolicyGradient(build_actor(env, **cfg.actor), build_critic(env, **cfg.critic), device=DEVICE, **cfg.ddpg)
    policy = ColoredNoisePolicy(algorithm.actor, env.action_space, env.spec.max_episode_steps, rng=rng, device=DEVICE, **cfg.noise)

    # Data pipeline
    memory = build_replay_buffer(env, device=DEVICE, error_fn=lambda x: torch.abs(algorithm.td_error(*x)).detach().cpu().numpy(), **cfg.memory)
    collector = build_collector(policy, env, memory, device=DEVICE)
    evaluator.policy = policy

    # Register logging and checkpoints
    logger.register_models([(algorithm.actor.net, "actor"), (algorithm.critic.net, "critic")])
    logger.register_providers([(algorithm, "train"), (collector, "collector"), (evaluator, "evaluation"), (memory, "memory")])

    # Training Loop
    pbar = tqdm(total=cfg.train.total_frames // cfg.log.frames_per_epoch, file=sys.stdout)
    collector.early_start(cfg.train.initial_collection_size)
    for step in range(1,  cfg.train.total_frames+1):
        collector.collect(n=1)

        # RL Update
        batch, weights = memory.sample(cfg.train.minibatch_size)
        batch = Transition(*batch)
        algorithm.update(batch, weights, step=step)

        # Epoch Logging
        if  step % cfg.log.frames_per_epoch == 0:
            critic_value =  algorithm.critic(torch.cat((evaluator.saved_reset_states, algorithm.actor(evaluator.saved_reset_states)), 1)).mean().item()
            logger.log({"train/critic_value": critic_value})
            reward, epoch = evaluator.evaluate(policy), logger.epoch()
            pbar.set_description(f"epoch {epoch} reward {reward}"), pbar.update(1)
        # Update checkpoints
        if step % cfg.log.checkpoint.frames_per_checkpoint == 0:
            logger.checkpoint_registered(step)

    # Close resources
    env.close(), evaluator.close(), logger.close(), pbar.close()

if __name__ == "__main__":
    train()
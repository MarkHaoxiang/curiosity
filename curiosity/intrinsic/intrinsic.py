from abc import ABC, abstractmethod
from typing import Optional

import torch
from torch import Tensor

from curiosity.experience import AuxiliaryMemoryData, Transition
from curiosity.logging import Loggable
from curiosity.util.stat import RunningMeanVariance

class IntrinsicReward(Loggable, ABC):
    """ Interface for intrinsic exploration rewards
    """
    def __init__(self,
                 int_coef: float = 1,
                 ext_coef: float = 1,
                 reward_normalisation: bool = False,
                 normalised_obs_clip: Optional[float] = None,
                 **kwargs) -> None:
        """ Initialises an intrinsic reward module

        Args:
            int_coef (float, optional): Intrinsic reward scaling factor. Defaults to 1.
            ext_coef (float, optional): Extrinsic reward scaling factor. Defaults to 1.
            reward_normalisation (bool, optional): Normalise output reward with unit variance. Defaults to False.
            normalised_obs_clip (float, optional): Clip observations. Introduced in RND. Defaults to 5.
        """
        super().__init__()
        self.info = {}
        # Intrinsic Reward Weighting
        self._int_coef = int_coef
        self._ext_coef = ext_coef

        # Normalisation
        self._reward_normalisation = RunningMeanVariance() if reward_normalisation else False
        self._normalised_obs_clip = normalised_obs_clip

    def _clip_batch(self, batch: Transition):
        if not self._normalised_obs_clip is None:
            s_0 = torch.clamp(batch.s_0, -self._normalised_obs_clip, self._normalised_obs_clip)
            s_1 = torch.clamp(batch.s_1, -self._normalised_obs_clip, self._normalised_obs_clip)
            batch = Transition(s_0, batch.a, batch.r, s_1, batch.d)
        return batch

    def reward(self, batch: Transition):
        """ Calculates the intrinsic exploration reward

        Args:
            batch (Transition): Batch of transition tuples from experience
        """
        batch = self._clip_batch(batch)
        r_i = self._reward(batch)
        if self._reward_normalisation:
            r_i = r_i / self._reward_normalisation.std.unsqueeze(0)
        self.info["mean_intrinsic_reward"] = torch.mean(r_i).item()
        self.info["max_intrinsic_reward"] = torch.max(r_i).item()
        self.info["mean_extrinsic_reward"] = torch.mean(batch.r)
        return (
            batch.r * self._ext_coef + r_i * self._int_coef, # Total Reward
            batch.r * self._ext_coef,                        # Extrinsic Reward (Scaled)
            r_i * self._int_coef                             # Intrinsic Reward
        )

    @abstractmethod
    def _reward(self, batch: Transition):
        raise NotImplementedError
    
    def update(self, batch: Transition, aux: AuxiliaryMemoryData, step: int):
        """ Intrinsic reward module update

        Args:
            batch (Transition): Batch of transition tuples from experience
            aux (AuxiliaryMemoryData): Auxiliary data for updates, such as weights and labels.
            step (int): Training Step
        """
        batch = self._clip_batch(batch)
        # Update Reward Normalisation
        if self._reward_normalisation:
            rewards = self._reward(batch)
            self._reward_normalisation.add_tensor_batch(rewards, aux.weights)
        # Update assistance networks
        self._update(batch, aux, step)

    @abstractmethod
    def _update(self, batch: Transition, aux: AuxiliaryMemoryData, step: int):
        raise NotImplementedError

    def initialise(self, batch: Transition):
        """ Initialise internal state (eg. for normalisation)

        Args:
            batch: Transition
        """
        batch = self._clip_batch(batch)
        if self._reward_normalisation:
            rewards = self._reward(batch)
            self._reward_normalisation.add_tensor_batch(rewards)

    def get_log(self):
        return self.info

class NoIntrinsicReward(IntrinsicReward):
    def _reward(self, batch: Transition):
        return torch.zeros(batch.r.shape[0], device=batch.r.device)
    def _update(self, batch: Transition, weights: Tensor, step: int):
        pass

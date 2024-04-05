from abc import ABC, abstractmethod
import math
from typing import Any

import numpy as np
import torch
from torch import Tensor

from kitten.nn import Actor, StochasticActor, HasActor
from kitten.experience import Transitions
from kitten.common.rng import Generator
from .interface import Algorithm, AuxiliaryData


class AdvantageEstimation(ABC):
    @abstractmethod
    def A(self, batch: Transitions) -> Tensor:
        raise NotImplementedError

    def update(self, batch: Transitions, aux: AuxiliaryData, step: int):
        pass


class ProximalPolicyOptimisation(Algorithm[AuxiliaryData], HasActor):
    def __init__(
        self,
        actor: StochasticActor,
        advantage_estimation: AdvantageEstimation,
        rng: Generator,
        update_epochs: int = 4,
        minibatch_size: int = 32,
        clip_ratio: float = 0.1,
        lr: float = 1e-3,
        **kwargs,
    ) -> None:
        self._actor = actor
        self._advantage_estimation = advantage_estimation
        self._update_epochs = update_epochs
        self._minibatch_size = minibatch_size
        self._clip_ratio = clip_ratio 
        self._optim = torch.optim.Adam(params=self._actor.parameters(), lr=lr)
        self._rng = rng.numpy
        self._loss = torch.nn.MSELoss()

    def update(self, batch: Transitions, aux: AuxiliaryData, step: int) -> Any:
        # Calculate Advantage
        self._advantage_estimation.update(batch, aux, step)
        a_hat = self._advantage_estimation.A(batch)
        # Update Policy
        n = len(
            batch.s_0
        )  # TODO: Some method to assert flat and batch size other than batch.s_0
        n_minibatches = math.ceil(n / self._minibatch_size)
        with torch.no_grad():
            log_prob_orig = self._actor.log_prob(batch.s_0, batch.a)

        total_loss = 0
        for _ in range(self._update_epochs):
            indices = np.arange(n)
            self._rng.shuffle(indices, axis=0)
            for i in range(n_minibatches):
                mb_indices = indices[
                    i * self._minibatch_size, min(n, (i + 1) * self._minibatch_size)
                ]
                mb = batch[mb_indices]
                a_hat_mb = a_hat[mb_indices]
                log_prob_update = self._actor.log_prob(mb.s_0, mb.a)
                ratio = (log_prob_update / log_prob_orig)
                actor_loss = -torch.minimum(
                    ratio * a_hat_mb,
                    torch.clamp(ratio, 1-self._clip_ratio, 1+self._clip_ratio) * a_hat_mb
                ).mean()
                self._optim.zero_grad()
                total_loss += actor_loss.item()
                actor_loss.backward()
                self._optim.step()

        return total_loss

    @property
    def actor(self) -> Actor:
        return self._actor

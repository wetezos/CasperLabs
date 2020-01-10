package io.casperlabs.casper.highway

import io.casperlabs.crypto.Keys.{PublicKey, PublicKeyBS}
import io.casperlabs.models.Message
import io.casperlabs.storage.BlockHash
import simulacrum.typeclass

/** Some sort of stateful, memoizing implementation of a fast fork choice.
  * Should have access to the era tree to check what are the latest messages
  * along the path, who are the validators in each era, etc.
  */
@typeclass
trait ForkChoice[F[_]] {

  /** Execute the fork choice based on a key block of an era:
    * - go from the key block to the switch block of the era, using the validators in that era
    * - go from the switch block using the next era's validators to the end of the next era
    * - repeat until the we arrive at the tips
    * - return the fork choice block along with all the justifications taken into account.
    */
  def fromKeyBlock(keyBlockHash: BlockHash): F[ForkChoice.Result]

  // There will be another method to validate a fork choice of an incoming block,
  // which will have as input the justifications of the message.
}
object ForkChoice {
  case class Result(
      mainParent: Message,
      // The fork choice must take into account messages from the parent
      // era's voting period as well, in order to be able to tell which
      // switch block in the end of the era to build on, and so which
      // blocks in the child era to follow. The new block we build
      // on top of the main parent can cite all these justifications.
      justifications: Set[Message]
  ) {
    def justificationsMap: Map[PublicKeyBS, Set[BlockHash]] =
      justifications.toSeq
        .map(j => PublicKey(j.validatorId) -> j.messageHash)
        .groupBy(_._1)
        .mapValues(_.map(_._2).toSet)
  }
}

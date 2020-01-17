import * as CL from "../../../../contract-ffi-as/assembly";
import {Error, ErrorCode, PosErrorCode} from "../../../../contract-ffi-as/assembly/error";
import {CLValue} from "../../../../contract-ffi-as/assembly/clvalue";
import {Key} from "../../../../contract-ffi-as/assembly/key";
import {PurseId} from "../../../../contract-ffi-as/assembly/purseid";
import {U512} from "../../../../contract-ffi-as/assembly/bignum";

const POS_ACTION = "bond";

export function call(): void {
    let proofOfStake = CL.getSystemContract(CL.SystemContract.ProofOfStake);
    if (proofOfStake === null) {
        Error.fromErrorCode(ErrorCode.InvalidSystemContract).revert();
        return;
    }

    let mainPurse = PurseId.getMainPurse();
    if (mainPurse === null) {
        Error.fromErrorCode(ErrorCode.MissingArgument).revert();
        return;
    }

    let bondingPurse = PurseId.createPurse();
    if (bondingPurse === null) {
        Error.fromErrorCode(ErrorCode.PurseNotCreated).revert();
        return;
    }

    let bond_amount = CL.getArg(0);
    if (bond_amount === null) {
        Error.fromErrorCode(ErrorCode.MissingArgument).revert();
        return;
    }

    let amount = U512.fromBytes(bond_amount);
    if (amount === null) {
        Error.fromErrorCode(ErrorCode.InvalidArgument).revert();
        return;
    }

    let ret = mainPurse.transferToPurse(
        <PurseId>(bondingPurse),
        amount,
    );
    if (ret > 0) {
        Error.fromErrorCode(ErrorCode.Transfer).revert();
        return;
    }

    let bondingPurseValue = CLValue.fromURef(bondingPurse.asURef());
    let key = Key.fromURef(proofOfStake);
    let args: CLValue[] = [
        CLValue.fromString(POS_ACTION),
        CLValue.fromU512(<U512>amount),
        bondingPurseValue
    ];

    let extraUrefs: Key[] = [Key.fromURef(bondingPurse.asURef())];
    let output = CL.callContractExt(key, args, extraUrefs);
    if (output === null) {
        Error.fromPosErrorCode(PosErrorCode.BondTransferFailed).revert();
        return;
    }
}

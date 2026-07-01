import { Module } from "@nestjs/common";
import { MongooseModule } from "@nestjs/mongoose";
import { dataAccessModels } from "./data-access.models";

@Module({
  imports: [
    MongooseModule.forFeature(dataAccessModels),
  ],
  exports: [
    MongooseModule.forFeature(dataAccessModels),
  ],
})
export class DataAccessModule {}
